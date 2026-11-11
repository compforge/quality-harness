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
Python Node/Relation constructors require the corresponding enum type. JSON decoding
constructs enum values at the boundary, including unknown custom kinds.

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

Nodes use the IDs supplied by models implementing `Identity.id()`. Graph does not
own another identity algorithm or export service/operation ID helpers.

- `Service.id()`: `service:<escaped-name>`, scoped to one environment's service catalog.
- `Operation.id()`: `operation:<escaped-service>/<escaped-name>`, using its required
  `service_name` (Python) / `serviceName` (TypeScript) and `name`.
- `HttpOperation` inherits the identity; method/path are transport details.

Each segment uses UTF-8 percent encoding (only RFC 3986 unreserved characters remain
literal). Names must be nonempty when requesting an ID. Projects must keep service
names unique within the environment catalog. These local IDs are not global Service
identity: Component and Environment remain part of Service's full runtime identity,
and the graph/run supplies that scope. Renaming changes the local ID.

Python Service and Operation implement the existing Identity protocol. TypeScript
Service, Operation and HttpOperation are concrete classes implementing Identity;
construct them from their declaration fields, including when restoring JSON.
Methods are not persisted. OperationRun's Service is the runtime binding, and the
operation's service name must match it. Python checks this at construction. In Perf's
OperationRun record, the enclosing service supplies the owner when reconstructing the
operation; it is not duplicated inside the operation payload.

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
from harness_common import (
    Component, Environment, Forge, Graph, Node, NodeKind, Operation,
    Relation, RelationKind, Repository, Service,
)

environment = Environment("qa")
component = Component(Repository(Forge("github"), "example/product"), "backend")
chat_service = Service("chat-server", component, environment)
memory_service = Service("memory-server", component, environment)
chat = Operation("chat", service_name=chat_service.name)
retrieve = Operation("retrieve", service_name=memory_service.name)
graph = Graph(
    environment=environment.name, source="project:topology", version="revision-1",
    nodes=(Node(chat_service.id(), NodeKind.SERVICE),
           Node(memory_service.id(), NodeKind.SERVICE),
           Node(chat.id(), NodeKind.OPERATION), Node(retrieve.id(), NodeKind.OPERATION)),
    relations=(Relation(chat.id(), chat_service.id(), RelationKind.BELONGS_TO),
               Relation(retrieve.id(), memory_service.id(), RelationKind.BELONGS_TO),
               Relation(chat.id(), retrieve.id(), RelationKind.CALLS)),
)
reachable = graph.reachable_from(chat.id(), kind=RelationKind.CALLS)
view = graph.subgraph(node.id for node in reachable)
saved = graph.to_dict()
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
invocation and can be associated with an operation node through `operation.id()`.

Metrics are collected independently and later joined by node or relation ID, environment
and observation window. Missing metrics remain unobserved, not zero. Consumers save the
Graph snapshot alongside run evidence so analysis can be reproduced offline. Declared
relationships and trace-observed relationships need separately identified sources and
coverage; neither a path nor correlated metrics alone proves causality. Domain consumers
construct operations with the owning service name; Perf scheduling, metrics and evaluation
behavior are outside this change.
