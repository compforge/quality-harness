"""Versioned call-graph snapshots derived from saved topology and MetricStore facts."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Literal
from urllib.parse import unquote

from harness_common import Node, NodeKind, Relation, RelationKind

from perf_harness.metric import Missing, ScalarSummary, parse_ref
from perf_harness.metric.store import MetricStore
from perf_harness.model import RED_UNITS, ArmRun, Run, Window


@dataclass(frozen=True)
class QueryEvidence:
    expression: str
    source_url: str
    start_s: float
    end_s: float
    collected_at_s: float | None
    error: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class CallGraphMetric:
    value: float | str | None
    unit: str
    status: Literal["observed", "missing", "error"]
    metric_ref: str | None
    reason: str | None = None
    queries: tuple[QueryEvidence, ...] = ()
    description: str = ""
    histogram: dict | None = None


@dataclass(frozen=True)
class CallGraphWindow:
    id: str
    kind: str
    phase: str | None
    name: str
    start_s: float
    end_s: float
    complete: bool
    limited_s: float
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class CallGraphArm:
    id: str
    arm_id: str
    started_at_s: float | None
    load: dict
    resources: dict
    windows: tuple[CallGraphWindow, ...]


@dataclass(frozen=True)
class CallGraphSnapshot:
    """One run's declared operation graph enriched with window-aligned measurements.

    @rule This is a derived reference artifact, not an observed-call trace or a verdict.
    @spec Numbers and missing/error states are preserved independently of display labels.
    """

    run_id: str
    experiment: str
    created_at: str
    status: str
    environment: str
    case_ids: tuple[str, ...]
    graph_source: str | None
    graph_version: str | None
    relations: tuple[Relation, ...]
    arms: tuple[CallGraphArm, ...]
    schema_version: int = field(default=2, init=False)
    topology_source: str = field(default="declared", init=False)

    def to_dict(self) -> dict:
        # Node/Relation properties are immutable mappings; serialize through Common.
        data = {
            "schema_version": self.schema_version,
            "topology_source": self.topology_source,
            "run_id": self.run_id,
            "experiment": self.experiment,
            "created_at": self.created_at,
            "status": self.status,
            "environment": self.environment,
            "case_ids": self.case_ids,
            "graph_source": self.graph_source,
            "graph_version": self.graph_version,
            "relations": [r.to_dict() for r in self.relations],
            "arms": [
                {
                    "id": arm.id,
                    "arm_id": arm.arm_id,
                    "started_at_s": arm.started_at_s,
                    "load": arm.load,
                    "resources": arm.resources,
                    "windows": [
                        {
                            "id": window.id,
                            "kind": window.kind,
                            "phase": window.phase,
                            "name": window.name,
                            "start_s": window.start_s,
                            "end_s": window.end_s,
                            "complete": window.complete,
                            "limited_s": window.limited_s,
                            "nodes": [node.to_dict() for node in window.nodes],
                        }
                        for window in arm.windows
                    ],
                }
                for arm in self.arms
            ],
        }
        return json.loads(json.dumps(data, allow_nan=False))


def _metric(
    store: MetricStore,
    arm: ArmRun,
    window: Window,
    address: str | None,
    unit: str,
    description: str = "",
) -> CallGraphMetric:
    if address is None:
        return CallGraphMetric(None, unit, "missing", None, "unbound", description=description)
    family, labels, _ = parse_ref(address)
    queries = [
        q
        for q in arm.prometheus_observations
        if q.metric == family
        and q.window_id == window.id
        and all(q.labels.get(k) == v for k, v in labels.items())
    ]
    evidence = tuple(
        QueryEvidence(
            q.expression, q.source_url, q.start_s, q.end_s, q.collected_at_s, q.error, q.warnings
        )
        for q in queries
    )
    value = store.query(arm, address, window)
    if isinstance(value, Missing):
        failed = any(
            q.error and not (q.data is not None and q.data.get("result") == [] and not q.warnings)
            for q in queries
        )
        empty = bool(queries) and all(
            q.data is not None
            and not q.warnings
            and (q.data.get("result") == [] or (q.histogram is not None and not q.error))
            for q in queries
        )
        return CallGraphMetric(
            None,
            unit,
            "error" if failed else "missing",
            address,
            "query_error" if failed else "no_samples" if empty else value.reason,
            evidence,
            description,
        )
    summary = window.probe_metrics.get(address.removesuffix(".value"))
    return CallGraphMetric(
        "+Inf" if value == math.inf else value,
        unit,
        "observed",
        address,
        queries=evidence,
        description=description,
        histogram=summary.histogram if isinstance(summary, ScalarSummary) else None,
    )


def build_callgraph(run: Run) -> CallGraphSnapshot | None:
    """Read already reduced facts; generating or comparing snapshots never sends load."""
    if run.graph is None:
        return None
    graph = run.graph
    bindings = {
        binding.target: binding for binding in run.observation_bindings if binding.workload is None
    }
    owners = {
        r.source: unquote(r.target.removeprefix("service:"))
        for r in graph.relations
        if r.kind == RelationKind.BELONGS_TO
    }
    operations = [node for node in graph.nodes if node.kind == NodeKind.OPERATION]
    store = MetricStore(run.arm_runs)
    arms = []
    for arm in run.arm_runs:
        windows = []
        for window in sorted(
            arm.windows, key=lambda w: (w.kind != "full", w.kind != "phase", w.start_s)
        ):
            nodes = []
            for node in operations:
                binding = bindings.get(node.id)
                observed = binding.metrics if binding else {}
                metrics = {
                    name: asdict(
                        _metric(
                            store,
                            arm,
                            window,
                            observed[name].metric if name in observed else None,
                            observed[name].unit if name in observed else RED_UNITS[name],
                            observed[name].description if name in observed else "",
                        )
                    )
                    | {"semantics": observed[name].semantics if name in observed else ""}
                    for name in dict.fromkeys([*RED_UNITS, *observed])
                }
                # Perf owns one property namespace; the source graph remains unchanged.
                props = dict(node.properties) | {
                    "perf": {
                        "name": unquote(node.id.split("/", 1)[-1]),
                        "service": owners[node.id],
                        "method": binding.method if binding else node.properties.get("method", ""),
                        "path": binding.path if binding else node.properties.get("path", ""),
                        "response_mode": (
                            binding.response_mode
                            if binding and binding.response_mode
                            else node.properties.get("response_mode")
                        ),
                        "qps_semantics": observed["qps"].semantics if "qps" in observed else None,
                        "metrics": metrics,
                    }
                }
                nodes.append(Node(node.id, node.kind, properties=props))
            windows.append(
                CallGraphWindow(
                    window.id,
                    window.kind,
                    window.phase,
                    window.name,
                    window.start_s,
                    window.end_s,
                    window.complete,
                    window.limited_s,
                    tuple(nodes),
                )
            )
        load = asdict(arm.arm.load)
        for entry in [load, *load["stages"]]:
            if math.isinf(entry["request_rate"]):
                entry["request_rate"] = "inf"
        arms.append(
            CallGraphArm(
                arm.id,
                arm.arm.id,
                arm.started_at_s,
                load,
                asdict(arm.arm.resources),
                tuple(windows),
            )
        )
    return CallGraphSnapshot(
        run.run_id,
        run.experiment,
        run.created_at,
        run.status,
        run.environment,
        tuple(run.case_ids),
        graph.source,
        graph.version,
        graph.calls,
        tuple(arms),
    )
