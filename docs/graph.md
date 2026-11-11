# System Graph

Common describes offline topology with `Graph`, `Node(id, kind)` and
`Relation(id, source, target, kind)`. Python and TypeScript implement the same wire
contract and consume `conformance/common/graphs.json`. Kinds are nonempty strings;
consumers can introduce additional kinds without introducing node subclasses.

## Open kinds

`NodeKind` and `RelationKind` expose built-in names while accepting custom nonempty
strings. Python uses open `StrEnum` values: `NodeKind.SERVICE`, `NodeKind.OPERATION`,
`RelationKind.BELONGS_TO`, `RelationKind.CALLS`; `NodeKind("model")` and
`RelationKind("uses")` preserve unknown values without registering new members.
Existing raw string arguments remain supported. Nodes and relations normalize their
kind into the corresponding enum on construction and loading.

TypeScript exports frozen constants `NodeKind.Service`, `NodeKind.Operation`,
`RelationKind.BelongsTo`, `RelationKind.Calls` and same-named open string types.
Their literal-plus-string unions retain editor completion while allowing custom kinds.

Both languages serialize kinds as the same plain strings. Unknown kinds roundtrip
unchanged, and known strings/constants follow identical validation rules. No enum
registration or wire schema change is required to extend the vocabulary.

## Scope and identity

A Graph is one immutable snapshot in a named environment. It has no fixed root.
Optional `source` and `version` identify where the declaration came from and which
revision it describes. They are provenance, not evidence that the declaration is
complete or matches current traffic. The wire format carries `schema_version: 1`;
`version` is the consumer's topology revision, not the format version.

Nodes initially use `service` and `operation`. Existing Service and Operation
models are unchanged. Name-derived node IDs use these helpers:

- Python `service_node_id(name)` / TypeScript `serviceNodeId(name)`:
  `service:<escaped-name>`.
- Python `operation_node_id(service, operation)` / TypeScript
  `operationNodeId(service, operation)`: `operation:<escaped-service>/<escaped-operation>`.

Each segment uses UTF-8 percent encoding (only RFC 3986 unreserved characters
remain literal), preventing slash and percent collisions. Names must be nonempty.
The project must ensure service names are unique within the graph's environment;
renaming changes identity. Node IDs are opaque to Graph validation and traversal.
A future identity scheme can replace these helpers without changing the graph model.

## Relations and validation

| Kind | Direction | Contract |
| --- | --- | --- |
| `belongs_to` | operation → service | Every operation has exactly one owner. |
| `calls` | operation → operation | Declared possible call; cycles and shared callees are allowed. |

Node IDs must be unique; relation endpoints must exist. Relations are unique by
`(source, kind, target)`. The relation ID is the compact JSON encoding of that triple,
with Unicode preserved; callers use Python `Relation(source, target, kind)` or
TypeScript `createRelation(source, target, kind)` to generate it. Loading rejects
mismatched IDs. Repeated calls or retries do not create parallel static edges;
counts, timings and individual spans belong to independent observations.

Unknown kinds are retained; built-in kinds keep their endpoint/ownership rules.
Graph construction validates the complete input atomically. Build node and relation
collections first, then construct a Graph; there is no separate Builder lifecycle.
An empty graph is valid and does not assert that the environment has no services.

## Build and query

```python
from harness_common import Graph, Node, Relation, operation_node_id, service_node_id

chat_service = service_node_id("chat-server")
memory_service = service_node_id("memory-server")
chat = operation_node_id("chat-server", "chat")
retrieve = operation_node_id("memory-server", "retrieve")
graph = Graph(
    environment="qa", source="project:topology", version="revision-1",
    nodes=(Node(chat_service, "service"), Node(memory_service, "service"),
           Node(chat, "operation"), Node(retrieve, "operation")),
    relations=(Relation(chat, chat_service, "belongs_to"),
               Relation(retrieve, memory_service, "belongs_to"),
               Relation(chat, retrieve, "calls")),
)
reachable = graph.reachable_from(chat, kind="calls")
view = graph.subgraph(node.id for node in reachable)
saved = graph.to_dict()  # serialize with json.dumps; no I/O occurs in common
restored = Graph.from_dict(saved)
```

TypeScript exposes `new Graph({ environment, nodes, relations, source?, version? })`,
`node`, `relationsFrom`, `relationsTo`, `reachableFrom`, `subgraph`, `toJSON` and
`Graph.fromJSON`. Python uses the corresponding snake_case methods. Graphs copy
input collections and retain immutable nodes/relations; serialized results are detached.

Queries preserve declaration order. Reachability includes the root, visits each node
once and terminates on cycles. A missing root or requested subgraph node is an error;
a missing node lookup returns None/undefined, and missing-node adjacency is empty.
Filter by `calls` to follow API dependencies without following ownership edges.
`subgraph` includes selected operations' service owners and all edges between retained
nodes, without pulling in unrelated operations belonging to those services.

## Evidence and ownership

The project owns topology declarations. Common owns the structure and deterministic
queries, not discovery, live access, metrics, diagnosis or a graph database.
Case describes test intent; it does not contain a topology. OperationRun is an actual
invocation and can be associated with an operation node by its service/operation names.

Metrics are collected independently and later joined by node or relation ID, environment
and observation window. Missing metrics remain unobserved, not zero. Consumers save the
Graph snapshot alongside run evidence so analysis can be reproduced offline. Declared
relationships and trace-observed relationships need separately identified sources and
coverage; neither a path nor correlated metrics alone proves causality. No Perf behavior
or API is changed by this Common addition.
