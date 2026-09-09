"""AgentRun IR —— 从完整 Node Tree 提取的 agent 语义中间表示。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from trace_harness.model.context import TraceContext

AGENT_RUN_SCHEMA = "trace-harness/agent-run@1"


@dataclass(frozen=True)
class ModelCall:
    id: str
    name: str
    start_ms: float
    duration_ms: float
    model: str | None = None
    status: str = ""
    input: Any = None
    output: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source_node_ids: tuple[str, ...] = ()
    kind: Literal["model-call"] = field(default="model-call", init=False)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    start_ms: float
    duration_ms: float
    tool_call_id: str | None = None
    status: str = ""
    input: Any = None
    output: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source_node_ids: tuple[str, ...] = ()
    agent_runs: tuple[AgentRun, ...] = ()
    kind: Literal["tool-call"] = field(default="tool-call", init=False)


@dataclass(frozen=True)
class Operation:
    id: str
    name: str
    start_ms: float
    duration_ms: float
    status: str = ""
    input: Any = None
    output: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source_node_ids: tuple[str, ...] = ()
    operations: tuple[Operation, ...] = ()
    agent_runs: tuple[AgentRun, ...] = ()
    kind: Literal["operation"] = field(default="operation", init=False)


TurnItem = ModelCall | ToolCall | Operation


@dataclass(frozen=True)
class AgentTurn:
    id: str
    start_ms: float
    duration_ms: float
    items: tuple[TurnItem, ...]
    name: str = ""
    status: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    source_node_ids: tuple[str, ...] = ()
    kind: Literal["agent-turn"] = field(default="agent-turn", init=False)


AgentRunItem = AgentTurn | Operation


@dataclass(frozen=True)
class AgentRun:
    id: str
    name: str
    start_ms: float
    duration_ms: float
    items: tuple[AgentRunItem, ...]
    status: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    source_node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentRunIR:
    trace_id: str
    runs: tuple[AgentRun, ...]
    schema: Literal["trace-harness/agent-run@1"] = field(
        default=AGENT_RUN_SCHEMA,
        init=False,
    )


def validate_agent_run_ir(ir: AgentRunIR, context: TraceContext) -> AgentRunIR:
    """Validate the extraction boundary before generic renderers consume the IR."""
    if ir.trace_id != context.trace_id:
        raise ValueError(
            f"AgentRunIR trace_id {ir.trace_id!r} does not match context {context.trace_id!r}"
        )

    known_nodes = {node.node_id for node in context.nodes}
    run_ids: set[str] = set()
    turn_ids: set[str] = set()
    item_ids: set[str] = set()

    def check_source_refs(owner: str, node_ids: tuple[str, ...]) -> None:
        missing = [node_id for node_id in node_ids if node_id not in known_nodes]
        if missing:
            raise ValueError(f"{owner} references unknown node IDs: {missing}")

    def check_timing(owner: str, start_ms: float, duration_ms: float) -> None:
        if not isinstance(start_ms, int | float) or not math.isfinite(start_ms):
            raise ValueError(f"{owner} has invalid start_ms: {start_ms!r}")
        if (
            not isinstance(duration_ms, int | float)
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            raise ValueError(f"{owner} has invalid duration_ms: {duration_ms!r}")

    def check_within(
        parent_owner: str,
        parent_start_ms: float,
        parent_duration_ms: float,
        child_owner: str,
        child_start_ms: float,
        child_duration_ms: float,
    ) -> None:
        if (
            child_start_ms < parent_start_ms
            or child_start_ms + child_duration_ms > parent_start_ms + parent_duration_ms
        ):
            raise ValueError(f"{child_owner} falls outside {parent_owner} time window")

    def check_runs(
        owner: str,
        runs: tuple[AgentRun, ...],
        field_name: str,
        parent_window: tuple[float, float] | None = None,
    ) -> None:
        previous_start: float | None = None
        for run in runs:
            if previous_start is not None and run.start_ms < previous_start:
                raise ValueError(f"{owner}.{field_name} must be ordered by start_ms")
            previous_start = run.start_ms
            check_run(run)
            if parent_window is not None:
                check_within(
                    owner,
                    *parent_window,
                    f"AgentRun {run.id}",
                    run.start_ms,
                    run.duration_ms,
                )

    def check_item(item: TurnItem) -> None:
        if item.id in item_ids:
            raise ValueError(f"duplicate turn item id: {item.id}")
        item_ids.add(item.id)
        check_timing(f"{type(item).__name__} {item.id}", item.start_ms, item.duration_ms)
        check_source_refs(f"{type(item).__name__} {item.id}", item.source_node_ids)
        if isinstance(item, Operation):
            previous_start: float | None = None
            for child in item.operations:
                if previous_start is not None and child.start_ms < previous_start:
                    raise ValueError(f"Operation {item.id}.operations must be ordered by start_ms")
                previous_start = child.start_ms
                check_item(child)
                check_within(
                    f"Operation {item.id}",
                    item.start_ms,
                    item.duration_ms,
                    f"Operation {child.id}",
                    child.start_ms,
                    child.duration_ms,
                )
        if isinstance(item, ToolCall | Operation):
            check_runs(
                f"{type(item).__name__} {item.id}",
                item.agent_runs,
                "agent_runs",
                (item.start_ms, item.duration_ms),
            )

    def check_run(run: AgentRun) -> None:
        if run.id in run_ids:
            raise ValueError(f"duplicate AgentRun id: {run.id}")
        run_ids.add(run.id)
        check_timing(f"AgentRun {run.id}", run.start_ms, run.duration_ms)
        check_source_refs(f"AgentRun {run.id}", run.source_node_ids)
        previous_run_item_start: float | None = None
        for run_item in run.items:
            if previous_run_item_start is not None and run_item.start_ms < previous_run_item_start:
                raise ValueError(f"AgentRun {run.id} items must be ordered by start_ms")
            previous_run_item_start = run_item.start_ms
            if isinstance(run_item, Operation):
                check_item(run_item)
                check_within(
                    f"AgentRun {run.id}",
                    run.start_ms,
                    run.duration_ms,
                    f"Operation {run_item.id}",
                    run_item.start_ms,
                    run_item.duration_ms,
                )
                continue

            turn = run_item
            if turn.id in turn_ids:
                raise ValueError(f"duplicate AgentTurn id: {turn.id}")
            turn_ids.add(turn.id)
            check_timing(f"AgentTurn {turn.id}", turn.start_ms, turn.duration_ms)
            check_source_refs(f"AgentTurn {turn.id}", turn.source_node_ids)
            check_within(
                f"AgentRun {run.id}",
                run.start_ms,
                run.duration_ms,
                f"AgentTurn {turn.id}",
                turn.start_ms,
                turn.duration_ms,
            )

            previous_item_start: float | None = None
            for item in turn.items:
                if previous_item_start is not None and item.start_ms < previous_item_start:
                    raise ValueError(f"AgentTurn {turn.id} items must be ordered by start_ms")
                previous_item_start = item.start_ms
                check_item(item)
                check_within(
                    f"AgentTurn {turn.id}",
                    turn.start_ms,
                    turn.duration_ms,
                    f"{type(item).__name__} {item.id}",
                    item.start_ms,
                    item.duration_ms,
                )

    check_runs("AgentRunIR", ir.runs, "runs")
    return ir


def _item_snapshot(item: TurnItem) -> dict[str, Any]:
    payload = {
        "kind": item.kind,
        "id": item.id,
        "name": item.name,
        "start_ms": item.start_ms,
        "duration_ms": item.duration_ms,
        "status": item.status,
        "input": item.input,
        "output": item.output,
        "attributes": item.attributes,
        "source_node_ids": list(item.source_node_ids),
    }
    if isinstance(item, ModelCall):
        payload["model"] = item.model
    elif isinstance(item, ToolCall):
        payload["tool_call_id"] = item.tool_call_id
    if isinstance(item, ToolCall | Operation):
        if isinstance(item, Operation):
            payload["operations"] = [_item_snapshot(child) for child in item.operations]
        payload["agent_runs"] = [_run_snapshot(run) for run in item.agent_runs]
    return payload


def _run_snapshot(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "name": run.name,
        "start_ms": run.start_ms,
        "duration_ms": run.duration_ms,
        "status": run.status,
        "attributes": run.attributes,
        "source_node_ids": list(run.source_node_ids),
        "items": [
            _item_snapshot(item)
            if isinstance(item, Operation)
            else {
                "kind": item.kind,
                "id": item.id,
                "name": item.name,
                "start_ms": item.start_ms,
                "duration_ms": item.duration_ms,
                "status": item.status,
                "attributes": item.attributes,
                "source_node_ids": list(item.source_node_ids),
                "items": [_item_snapshot(turn_item) for turn_item in item.items],
            }
            for item in run.items
        ],
    }


def agent_run_snapshot(ir: AgentRunIR) -> dict[str, Any]:
    """Return the canonical JSON-compatible AgentRun IR projection."""
    return {
        "schema": ir.schema,
        "trace_id": ir.trace_id,
        "runs": [_run_snapshot(run) for run in ir.runs],
    }
