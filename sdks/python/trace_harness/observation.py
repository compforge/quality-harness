"""Trace evidence constraints on the shared Session observation model."""

from collections.abc import Iterable, Iterator

from agent_ledger.observation import Node as ObservationNode
from agent_ledger.observation import Operation, SessionView, Turn
from spec_case import link, spec

from trace_harness.model.context import TraceContext


def walk_nodes(nodes: Iterable[ObservationNode]) -> Iterator[ObservationNode]:
    for item in nodes:
        yield item
        if isinstance(item, (Operation, Turn)):
            yield from walk_nodes(item.nodes)


def source_node_ids(item: ObservationNode, context: TraceContext) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            ref.id
            for ref in item.evidence
            if ref.source == "trace-node" and ref.uri in (None, f"trace:{context.trace_id}")
        )
    )


@spec("Extractors return shared SessionViews; Trace validates only local evidence ownership")
@link("repo://docs/session-view.md")
def validate_session_views(
    views: Iterable[SessionView], context: TraceContext
) -> tuple[SessionView, ...]:
    # Revalidate mutable model contents and detach them from extractor-owned data.
    result = tuple(SessionView.model_validate(view.model_dump(mode="json")) for view in views)
    seen: set[tuple[str, str]] = set()
    known = context.tree().by_id
    for view in result:
        identity = (view.session.source, view.session.id)
        if identity in seen:
            raise ValueError(f"Trace {context.trace_id}: duplicate Session {identity}")
        seen.add(identity)
        for item in walk_nodes(view.nodes):
            missing = set(source_node_ids(item, context)) - known.keys()
            if missing:
                raise ValueError(
                    f"Trace {context.trace_id}, Session {identity}, item {item.id}: "
                    f"unknown node IDs: {sorted(missing)}"
                )
    return result
