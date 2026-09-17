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

Service identifies a Component's runtime presence in an Environment. A Component belongs to a Repository
on a Forge; a Service explicitly declares zero or more Workloads. Consumers extend Service with business
capabilities, without duplicating these identities. For example:

```typescript
import type { Service } from "@compforge/harness-common";

const service: Service = {
  name: "chat",
  component: {
    name: "api",
    repository: { forge: { name: "github" }, path: "example/app" },
  },
  environment: { name: "test" },
  workloads: [{
    name: "web", platform: "kubernetes",
    location: { kind: "resource", resource_kind: "Deployment", name: "api-server" },
  }],
};
```

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
