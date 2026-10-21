# Harness Common for TypeScript

Execution-neutral service topology, client lifecycle, environment context and datasource contracts, shared by harnesses and toolbox clients.
No database drivers, Kubernetes adapters, Doctor concepts or business configuration live here.

```typescript
import { ClientManager, type DataSource } from "@compforge/harness-common";

const clients = new ClientManager();
try {
  const client = await clients.get(source); // source implements DataSource<Client>
  // Borrow the initialized client; the root execution owns cleanup.
} finally {
  await clients.dispose();
}
```

A ClientProvider clientKey identifies the complete configuration and access policy. Equal keys share client
initialization, not query results. Use clientKey to hash configuration without exposing credentials.
ServiceDataSource associates a shared Service (or a consumer extension) with a source without changing its key.
DataSource extends ClientProvider with data-access semantics; accessible concrete environments implement
ClientProvider directly. Both use the same manager. EnvironmentContext binds an Environment (or a consumer
extension), a get-only client view and monotonic deadlineMs (performance.now()).
It needs no fixture and owns neither disposal nor cancellation; forward remainingMs into operations.
Providers receive a get-only ClientManager view and the root AbortSignal. Acquire dependencies through that
view during initialize, so concurrent consumers share them and dispose before their dependencies.
Dependency graphs must be acyclic. A failed initialization permits retry only after successful cleanup;
failed cleanup poisons that key and is reported again when the root manager is disposed.

`FileDataSource` is an abstract, runtime-recognizable DataSource for one file path. Its `FileClient.read`
returns fresh `Uint8Array` bytes with explicit `maxBytes`, `timeoutMs` and optional cancellation.
An empty file succeeds; an oversized or incomplete read rejects. The source's `path` and concrete
location metadata let consumers attach provenance. Parsing, configuration precedence and persistence
belong to consumers. Platform implementations live in `@compforge/harness-toolbox/file`.

Service identifies a Component's runtime presence in an Environment. A Component belongs to a Repository
on a Forge. Product is a separate business identity; consumers own its many-to-many associations
with Components. Repository discovery and directory ownership remain consumer responsibilities.
A Service explicitly declares zero or more Workloads. Consumers extend Service with business
capabilities, without duplicating these identities. For example:

```typescript
import {
  repositoryId, conceptToMarkdown, knowledgeToMarkdown, stepToMarkdown, workflowToMarkdown,
  type Service,
} from "@compforge/harness-common";

const service: Service = {
  name: "chat",
  knowledge: {
    markdown() { return knowledgeToMarkdown(this); },
    concepts: [{ id: "conversation", name: "Conversation", description: "Contains Messages; each Message may have multiple execution Attempts.",
      markdown() { return conceptToMarkdown(this); } }],
    workflows: [{ markdown() { return workflowToMarkdown(this); }, id: "reply", title: "Reply", summary: "Accept and answer a message.", steps: [
      { id: "execute", title: "Execute", description: "Execute an attempt and persist its response.",
        markdown() { return stepToMarkdown(this); } },
    ] }],
  },
  upstream: ["gateway"],
  component: {
    name: "api",
    repository: { forge: { name: "github" }, path: "example/app", id() { return repositoryId(this); } },
  },
  environment: { name: "test" },
  workloads: [{
    name: "web", platform: "kubernetes",
    location: { kind: "resource", resource_kind: "Deployment", name: "api-server" },
  }],
};
```

Repository extends Identity: `id()` returns `forge.name/path`, excluding `forkedFrom`.
Use `repositoryId(this)` when implementing the method, including when restoring a Repository
from JSON; methods are not serialized.

Service and Product accept an optional description for a short introduction, and structured
`knowledge` for concepts, workflows, articles and dictionaries. Service knowledge covers its own
responsibilities, while Product knowledge covers end-to-end flows. Consumers maintain this offline
metadata; it does not change identity, execute a workflow or prove runtime state.

Service `upstream` lists the canonical names of Services that call it, resolved in the consumer's
catalog. Omission means the relationship is undeclared; `[]` declares no upstream callers.
This describes architecture; runtime traces determine which calls actually occurred.

Environment carries identity and an optional local/SSH Host declaration, not a connection. HostEnvironment
describes a target running directly on that host. Concrete accessible environments, such as toolbox's
KubernetesEnvironment, additionally implement ClientProvider. A Host declaration alone does not add SSH
support to a client. Service<E> and EnvironmentContext<E> preserve the concrete environment's access type.

Workload describes a logical runtime carrier and its resource, Kubernetes Service or labels location.
WorkloadInstance records an observed Pod incarnation with environment and UID identity.
These contracts perform no discovery or I/O; callers resolve declarations in their selected environment.
Container selection affects access routing, not Pod identity. See [the shared model](../../../docs/workload.md).

Python's corresponding package is sdks/python/common (harness-common). Languages keep idiomatic APIs
and independently versioned packages; they need not implement every model in lockstep.

Development: bun install, make lint, make test. Published artifacts are Node-compatible ESM and declarations.

## Structured knowledge

Product and Service accept an optional `knowledge` declaration. `Knowledge` groups
`Concept`, `Workflow` (with parent-scoped `Step` records), `Article` (with `Section`
records), and typed `Dictionary<Entry>` collections. All eight content interfaces extend
`Markdown`, whose `markdown(): string` method returns an I/O-free representation.
Declarations remain ordinary objects; methods can delegate to the shared converters below.
`KnowledgeSource` describes provenance and does not carry this rendering contract.

JSON preserves the content fields, but not methods. Consumers own source validation,
method restoration, distribution and version selection. Parsing JSON or asserting a type
does not create a callable Knowledge object; restore methods on each content object before
exposing it through these interfaces.

IDs are unique per kind within the owner, and per child collection within its parent.
Step `parentId` expresses inclusion; sibling order follows the array even when a parent
appears after its child. `validateKnowledge` rejects duplicate/empty IDs, unknown parents
and parent cycles before references are exposed. Dictionary entry IDs are unique, while
names and domain keys such as error codes may repeat. `source.version` describes the
published definitions independently of a containing distribution's version.

`knowledge.ts` also provides deterministic, I/O-free Markdown projections:
`knowledgeToMarkdown`, `conceptToMarkdown`, `workflowToMarkdown`, `stepToMarkdown`, `articleToMarkdown`,
`sectionToMarkdown`, `dictionaryToMarkdown` and `entryToMarkdown`.
Dictionary entries retain their typed domain fields; implement `Entry.markdown()` to
include these fields. The shared `entryToMarkdown` uses name, description and aliases.
Generated headings and ordered steps use compact spacing; authored paragraphs and code
blocks retain their content. Terminal presentation, exact lookup, search, source loading
and vector indexes belong to consumers.

## Upgrade to 0.4

Move the removed Product/Service `concepts` and `workflows` string fields into
`knowledge.concepts` and `knowledge.workflows`. Use Concept records for domain objects
and Workflow/Step records for business processes. Knowledge remains optional.

## Upgrade to 0.5

Implement `markdown()` on Knowledge and each contained Concept, Workflow, Step, Article,
Section, Dictionary and Entry. For example:

```typescript
import { conceptToMarkdown, type Concept } from "@compforge/harness-common";

const concept: Concept = {
  id: "carrier",
  name: "Carrier",
  description: "Infrastructure capacity.",
  markdown() { return conceptToMarkdown(this); },
};

console.log(concept.markdown());
```

Existing `...ToMarkdown` functions remain available as shared implementations. Each
parent calls its children's `markdown()` methods: Knowledge groups concepts, workflows,
articles and dictionaries; Article renders Sections; Dictionary renders Entries; Workflow
numbers and indents Steps according to `parentId`. Parents preserve child output instead
of rebuilding it from the child's fields. Helpers never call the receiver's own method,
so `markdown() { return knowledgeToMarkdown(this); }` does not recurse.

Default headings follow the composition tree: Knowledge categories use level 1; Concepts,
Workflows, Articles and Dictionaries use level 2; Sections and Entries use level 3.
Steps return a bold title and body, with numbering supplied by Workflow. Standalone output
uses the same format. Custom methods own their heading choices; parents do not rewrite
headings or code blocks in child output.

Move `KnowledgeMarkdownOptions.renderEntry` or `dictionaryToMarkdown` renderer callbacks
into each Entry's `markdown()` method. These callback arguments are removed in 0.5; a
custom Entry now renders consistently when shown alone or through its Dictionary and Knowledge.
