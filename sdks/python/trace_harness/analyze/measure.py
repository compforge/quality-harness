"""Whole-trace measurers, run once each with analysis-local state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import floor, isfinite

from trace_harness.kinds.http import http_requests
from trace_harness.loading.facts import EvidenceDependency, FactDependency
from trace_harness.model.context import TraceContext
from trace_harness.model.intervals import interval_union
from trace_harness.model.measurement import CallSource, Measurement, Measurements, MeasurementSpec


@dataclass(frozen=True)
class Measurer:
    spec: MeasurementSpec
    compute: Callable[[TraceContext, list[CallSource]], Iterable[Measurement]]
    requires: Callable[[TraceContext], tuple[EvidenceDependency | FactDependency, ...]] = (
        lambda t: ()
    )


CALLS = MeasurementSpec(
    "calls_until_node_end",
    "trace_prefix",
    {"count": "call", "duration_sum_ms": "ms", "covered_ms": "ms"},
    "Calls started from earliest observed trace start through anchor end; "
    "in-flight durations clipped. Kind coverage can overlap.",
    ("kind",),
)
SELF = MeasurementSpec(
    "self_ms",
    "node",
    {"self_ms": "ms"},
    "Node duration not covered by direct children, clipped to the node interval.",
)


def call_sources(trace: TraceContext) -> list[CallSource]:
    # Protocol ownership/pairing stays in kinds.http. All HTTP is included, even model/SSE.
    requests = http_requests(trace)
    claimed = {span.span_id for request in requests for span in request.spans}
    sources = [
        CallSource(
            request.span.span_id,
            "http",
            request.start_ms,
            request.end_ms,
            trace.view().by_span[request.span.span_id].node_id,
            tuple(span.span_id for span in request.spans),
        )
        for request in requests
    ]
    sources.extend(
        CallSource(
            node.node_id,
            node.kind,
            node.start_ms,
            node.end_ms,
            node.node_id,
            tuple(node.span_ids),
        )
        for node in trace.nodes
        if node.kind != "service"
        and not (node.kind == "http" and claimed.intersection(node.span_ids))
    )
    return sorted(sources, key=lambda source: (source.start_ms, source.kind, source.id))


def prefix_values(
    sources: list[CallSource], cutoffs: Iterable[float], start: float
) -> dict[float, dict]:
    """Sweep each kind once: integrate active count and active>0, then snapshot at cuts.

    Starts at a cut count immediately, including zero-duration calls. Shared source
    selectors retain provenance without copying every earlier call into every result.
    """
    cuts = sorted(set(cutoffs))
    if (
        not isfinite(start)
        or any(not isfinite(cut) for cut in cuts)
        or any(
            not isfinite(source.start_ms)
            or not isfinite(source.end_ms)
            or source.end_ms < source.start_ms
            for source in sources
        )
    ):
        raise ValueError("invalid measurement interval")
    result: dict[float, dict] = {cut: {} for cut in cuts}
    groups: dict[str, list[CallSource]] = defaultdict(list)
    for source in sources:
        groups[source.kind].append(source)
    for kind in sorted(groups):
        events: dict[float, list[int]] = defaultdict(lambda: [0, 0])
        for source in groups[kind]:
            events[source.start_ms][0] += 1
            events[source.end_ms][1] += 1
        times = sorted(events)
        index = active = count = 0
        total = covered = 0.0
        previous = start
        for cut in cuts:
            while index < len(times) and times[index] <= cut:
                time = times[index]
                elapsed = max(0, time - previous)
                total += elapsed * active
                covered += elapsed if active else 0
                starts, ends = events[time]
                count += starts
                active += starts - ends
                previous = time
                index += 1
            elapsed = max(0, cut - previous)
            result[cut][kind] = {
                "count": count,
                "duration_sum_ms": _rounded(total + elapsed * active),
                "covered_ms": _rounded(covered + (elapsed if active else 0)),
            }
    return result


def _calls(trace: TraceContext, sources: list[CallSource]) -> Iterable[Measurement]:
    start = min(
        [span.start_ms for span in trace.spans.values()] + [node.start_ms for node in trace.nodes],
        default=0,
    )
    values = prefix_values(sources, (node.end_ms for node in trace.nodes), start)
    for node in trace.nodes:
        yield Measurement(
            CALLS.id,
            node.node_id,
            "measured",
            values[node.end_ms],
            {"source": "calls", "start_ms": start, "end_ms": node.end_ms},
        )


def _rounded(value: float) -> float:
    # Match the non-negative millisecond rounding in the TypeScript implementation.
    return floor(value * 1000 + 0.5) / 1000


def _self(trace: TraceContext, sources: list[CallSource]) -> Iterable[Measurement]:
    for node in trace.nodes:
        if not isfinite(node.start_ms) or not isfinite(node.duration_ms) or node.duration_ms < 0:
            raise ValueError("invalid measurement interval")
        intervals = [
            (max(node.start_ms, child.start_ms), min(node.end_ms, child.end_ms))
            for child in trace.view().children(node)
        ]
        covered = interval_union([(start, end) for start, end in intervals if end > start])
        yield Measurement(
            SELF.id,
            node.node_id,
            "measured",
            {"self_ms": _rounded(max(0, node.duration_ms - covered))},
            {"source": "direct_children", "node_id": node.node_id},
        )


BUILTIN_MEASURERS = (Measurer(SELF, _self), Measurer(CALLS, _calls))


def measure(trace: TraceContext, measurers: Iterable[Measurer] = BUILTIN_MEASURERS) -> Measurements:
    items = tuple(measurers)
    if len({item.spec.id for item in items}) != len(items):
        raise ValueError("duplicate measurement id")
    sources = call_sources(trace)
    output = Measurements([item.spec for item in items], sources)
    for item in items:
        try:
            results = list(item.compute(trace, sources))
            by_node = {result.anchor_node_id: result for result in results}
            if len(by_node) != len(results) or any(
                result.spec_id != item.spec.id
                or result.anchor_node_id not in trace.view().by_id
                or result.status not in {"measured", "not_applicable", "error"}
                or (result.status != "measured" and bool(result.values))
                for result in results
            ):
                raise ValueError("invalid measurer output")
        except Exception as exc:
            # A failed computation is visible and has no numeric value; never substitute zero.
            by_node = {
                node.node_id: Measurement(item.spec.id, node.node_id, "error", error=str(exc))
                for node in trace.nodes
            }
        for node in trace.nodes:
            output.results.setdefault(node.node_id, []).append(
                by_node.get(node.node_id, Measurement(item.spec.id, node.node_id, "not_applicable"))
            )
    return output
