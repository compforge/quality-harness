"""Shared detector definitions, dependency planning and invocation semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from trace_harness.model.node import Finding

InputT = TypeVar("InputT")
ContextT = TypeVar("ContextT")
Detect = Callable[[InputT, ContextT], Iterable[Finding] | Awaitable[Iterable[Finding]]]


@dataclass(frozen=True)
class Detector(Generic[InputT, ContextT]):
    """Explicit identity and direct dependencies with a plain sync/async handler.

    Dependencies address the same execution unit, not another grain. A definition
    binds one configuration; the owner supplies Node or Dataset scheduling.
    """

    id: str
    detect: Detect[InputT, ContextT]
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetectorResult:
    """Completed execution with iterable structured output, independent of storage.

    A failed execution may have partial output. Business coverage is carried by
    findings; succeeded with no findings is not equivalent to failed execution.
    """

    id: str
    status: Literal["succeeded", "failed"]
    _read: Callable[[], Iterator[dict]] = field(repr=False, compare=False)
    error: dict[str, str] | None = None

    def findings(self) -> Iterator[dict]:
        return self._read()


def dependency_result(
    definition: Detector[InputT, ContextT] | None,
    completed: Mapping[str, DetectorResult],
    detector_id: str,
) -> DetectorResult:
    if definition is None:
        raise RuntimeError("dependency results are available only during detector execution")
    if detector_id not in definition.requires:
        raise KeyError(f"{definition.id!r} did not declare dependency {detector_id!r}")
    return completed[detector_id]


async def execute_detector(
    definition: Detector[InputT, ContextT],
    item: InputT,
    context: ContextT,
    *,
    emit: Callable[[Finding], None],
    read: Callable[[], Iterator[dict]],
) -> DetectorResult:
    """Invoke once; preserve partial output and make ordinary failures inspectable.

    Cancellation propagates. The unit owner controls storage and emission, so Node
    execution and Dataset execution share semantics without sharing lifetimes.
    """
    try:
        findings = definition.detect(item, context)
        if isinstance(findings, Awaitable):
            findings = await findings
        for finding in findings or ():
            emit(finding)
    except Exception as exc:
        return DetectorResult(
            definition.id, "failed", read, {"type": type(exc).__name__, "message": str(exc)}
        )
    return DetectorResult(definition.id, "succeeded", read)


def plan_detectors(
    definitions: Iterable[Detector[InputT, ContextT] | Detect[InputT, ContextT]],
    selected: list[str] | None,
) -> list[Detector[InputT, ContextT]]:
    """Validate definitions before I/O; expand dependencies once in stable order."""
    registry = {}
    for entry in definitions:
        # Existing function registrations remain a boundary shorthand. Explicit
        # definitions never derive identity from language-specific function names.
        item = entry if isinstance(entry, Detector) else Detector(entry.__name__, entry)
        if not item.id or item.id in registry:
            raise ValueError(f"empty or duplicate detector id: {item.id!r}")
        registry[item.id] = item
    visiting, done, order = [], set(), []

    def visit(name: str) -> None:
        if name not in registry:
            raise KeyError(f"unknown detectors: {name!r}")
        if name in visiting:
            raise ValueError(f"detector dependency cycle: {' -> '.join([*visiting, name])}")
        if name in done:
            return
        visiting.append(name)
        for dependency in registry[name].requires:
            visit(dependency)
        visiting.pop()
        done.add(name)
        order.append(registry[name])

    # Validate the registry independently of selection so latent invalid bindings
    # cannot become query-dependent failures after evidence has already been read.
    for name in registry:
        visit(name)
    done.clear()
    order.clear()
    for name in registry if selected is None else selected:
        visit(name)
    return order
