# Harness Common for TypeScript

Execution-neutral client lifecycle and datasource contracts, shared by harnesses and toolbox clients.
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

A DataSource key identifies the complete configuration and access policy. Equal keys share client
initialization, not query results. Use dataSourceKey to hash configuration without exposing credentials.
ServiceDataSource associates a consumer-owned Service model with a source without changing its key.

Python's corresponding package is sdks/python/common (harness-common). Languages keep idiomatic APIs
and independently versioned packages; they need not implement every model in lockstep.

Development: bun install, make lint, make test. Published artifacts are Node-compatible ESM and declarations.
