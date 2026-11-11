"""Offline system topology; collection and interpretation belong to consumers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Iterable, Self


class _OpenKind(StrEnum):
    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        # Unknown kinds are values, not global enum registrations.
        if not isinstance(value, str) or not value:
            return None
        member = str.__new__(cls, value)
        member._name_ = None
        member._value_ = value
        return member


class NodeKind(_OpenKind):
    """Built-in node kinds; nonempty custom strings remain valid enum values."""

    SERVICE = "service"
    OPERATION = "operation"


class RelationKind(_OpenKind):
    """Built-in relations; unknown values retain their string identity and roundtrip."""

    BELONGS_TO = "belongs_to"
    CALLS = "calls"


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: NodeKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, NodeKind):
            raise TypeError("node kind must be NodeKind")


@dataclass(frozen=True, slots=True)
class Relation:
    """A static directed relationship, not an individual call occurrence.

    @spec Identity is the ordered source/kind/target triple; repeated calls share an edge.
    """

    source: str
    target: str
    kind: RelationKind
    id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RelationKind):
            raise TypeError("relation kind must be RelationKind")
        if any(
            not isinstance(value, str) or not value
            for value in (self.source, self.target, self.kind)
        ):
            raise ValueError("relation endpoints and kind must be nonempty strings")
        object.__setattr__(
            self,
            "id",
            json.dumps(
                [self.source, self.kind, self.target],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Graph:
    """A validated immutable topology snapshot in one environment.

    @spec Kinds are extensible; belongs_to and calls retain their built-in endpoint rules.
    @spec Every operation belongs to exactly one service; call cycles are allowed.
    @rule Topology is not proof of actual traffic, metric coverage or causality.
    @link component://docs/graph.md
    """

    environment: str
    nodes: tuple[Node, ...] = ()
    relations: tuple[Relation, ...] = ()
    source: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "relations", tuple(self.relations))
        if not isinstance(self.environment, str) or not self.environment:
            raise ValueError("graph environment must not be empty")
        for value in (self.source, self.version):
            if value is not None and not isinstance(value, str):
                raise ValueError("graph source and version must be strings")
        nodes: dict[str, Node] = {}
        for node in self.nodes:
            if (
                not isinstance(node.id, str)
                or not node.id
                or not isinstance(node.kind, str)
                or not node.kind
            ):
                raise ValueError("node id and kind must be nonempty strings")
            if node.id in nodes:
                raise ValueError(f"duplicate node id: {node.id}")
            nodes[node.id] = node
        owners: dict[str, str] = {}
        ids: set[str] = set()
        for relation in self.relations:
            if not isinstance(relation.kind, str) or not relation.kind:
                raise ValueError("relation kind must be a nonempty string")
            if relation.id in ids:
                raise ValueError(f"duplicate relation: {relation.id}")
            ids.add(relation.id)
            if relation.source not in nodes or relation.target not in nodes:
                raise ValueError(f"unknown relation endpoint: {relation.id}")
            source, target = nodes[relation.source], nodes[relation.target]
            if relation.kind == RelationKind.CALLS and (source.kind, target.kind) != (
                NodeKind.OPERATION,
                NodeKind.OPERATION,
            ):
                raise ValueError(f"calls requires operation endpoints: {relation.id}")
            if relation.kind == RelationKind.BELONGS_TO:
                if (source.kind, target.kind) != (NodeKind.OPERATION, NodeKind.SERVICE):
                    raise ValueError(
                        f"belongs_to requires operation -> service: {relation.id}"
                    )
                if source.id in owners:
                    raise ValueError(f"operation has multiple services: {source.id}")
                owners[source.id] = target.id
        for node in self.nodes:
            if node.kind == NodeKind.OPERATION and node.id not in owners:
                raise ValueError(f"operation has no service: {node.id}")

    def node(self, node_id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def relations_from(
        self, node_id: str, *, kind: RelationKind | None = None
    ) -> tuple[Relation, ...]:
        return tuple(
            r
            for r in self.relations
            if r.source == node_id and (kind is None or r.kind == kind)
        )

    def relations_to(
        self, node_id: str, *, kind: RelationKind | None = None
    ) -> tuple[Relation, ...]:
        return tuple(
            r
            for r in self.relations
            if r.target == node_id and (kind is None or r.kind == kind)
        )

    def reachable_from(
        self, node_id: str, *, kind: RelationKind | None = None
    ) -> tuple[Node, ...]:
        """Return reachable nodes including root, in declaration order; cycles terminate."""
        if self.node(node_id) is None:
            raise ValueError(f"unknown root: {node_id}")
        seen = {node_id}
        pending = [node_id]
        adjacency: dict[str, list[str]] = {}
        for relation in self.relations:
            if kind is None or relation.kind == kind:
                adjacency.setdefault(relation.source, []).append(relation.target)
        while pending:
            for target in adjacency.get(pending.pop(), ()):
                if target not in seen:
                    seen.add(target)
                    pending.append(target)
        return tuple(node for node in self.nodes if node.id in seen)

    def subgraph(self, node_ids: Iterable[str]) -> Graph:
        """Induce a subgraph, retaining operation owners without adding their other APIs."""
        selected = set(node_ids)
        unknown = selected - {node.id for node in self.nodes}
        if unknown:
            raise ValueError(f"unknown nodes: {sorted(unknown)}")
        selected.update(
            r.target
            for r in self.relations
            if r.kind == RelationKind.BELONGS_TO and r.source in selected
        )
        return Graph(
            environment=self.environment,
            source=self.source,
            version=self.version,
            nodes=tuple(n for n in self.nodes if n.id in selected),
            relations=tuple(
                r
                for r in self.relations
                if r.source in selected and r.target in selected
            ),
        )

    def to_dict(self) -> dict:
        result = {
            "schema_version": 1,
            "environment": self.environment,
            "nodes": [asdict(n) for n in self.nodes],
            "relations": [asdict(r) for r in self.relations],
        }
        if self.source is not None:
            result["source"] = self.source
        if self.version is not None:
            result["version"] = self.version
        return result

    @classmethod
    def from_dict(cls, value: dict) -> Graph:
        """Restore a snapshot through the same validation as programmatic construction."""
        if not isinstance(value, dict):
            raise ValueError("expected graph object")
        if (
            isinstance(value.get("schema_version"), bool)
            or value.get("schema_version") != 1
        ):
            raise ValueError("unsupported graph schema_version")
        for key in ("source", "version"):
            if key in value and not isinstance(value[key], str):
                raise ValueError(f"graph {key} must be a string")
        for key in ("nodes", "relations"):
            if not isinstance(value.get(key), list):
                raise ValueError(f"expected graph {key} array")
            if any(not isinstance(item, dict) for item in value[key]):
                raise ValueError("expected node or relation object")
        relations = []
        for item in value["relations"]:
            relation = Relation(
                source=item.get("source"),
                target=item.get("target"),
                kind=RelationKind(item.get("kind")),
            )
            if item.get("id") != relation.id:
                raise ValueError(
                    f"relation id does not match endpoints/kind: {item.get('id')}"
                )
            relations.append(relation)
        return cls(
            environment=value.get("environment"),
            source=value.get("source"),
            version=value.get("version"),
            nodes=tuple(
                Node(id=n.get("id"), kind=NodeKind(n.get("kind")))
                for n in value["nodes"]
            ),
            relations=tuple(relations),
        )
