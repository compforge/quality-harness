# Harness Common for TypeScript

Execution-neutral client lifecycle, environment context and datasource contracts, shared by harnesses and toolbox clients.
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
ServiceDataSource associates a consumer-owned Service model with a source without changing its key.
DataSource extends ClientProvider with data-access semantics; accessible concrete environments implement
ClientProvider directly. Both use the same manager. EnvironmentContext binds a consumer-owned
environment, a get-only client view and monotonic deadlineMs (performance.now()).
It needs no fixture and owns neither disposal nor cancellation; forward remainingMs into operations.
Providers receive a get-only ClientManager view and the root AbortSignal. Acquire dependencies through that
view during initialize, so concurrent consumers share them and dispose before their dependencies.
Dependency graphs must be acyclic. A failed initialization permits retry only after successful cleanup;
failed cleanup poisons that key and is reported again when the root manager is disposed.

Workload describes a logical runtime carrier and its resource, Service or labels location.
WorkloadInstance records an observed Pod incarnation with environment and UID identity.
These contracts perform no discovery or I/O; callers resolve declarations in their selected environment.
Container selection affects access routing, not Pod identity. See [the shared model](../../../docs/workload.md).

Python's corresponding package is sdks/python/common (harness-common). Languages keep idiomatic APIs
and independently versioned packages; they need not implement every model in lockstep.

Development: bun install, make lint, make test. Published artifacts are Node-compatible ESM and declarations.
