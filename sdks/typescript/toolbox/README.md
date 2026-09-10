# Harness Toolbox

Shared infrastructure for diagnostic tools, test runners and business adapters. Use the same
clients to access Kubernetes, MySQL, Redis and OpenSearch without depending on a particular
command, plugin protocol, test model or verdict.

The package supports Node.js 22+ and Bun. Install with `npm install @compforge/harness-toolbox`.
Protocol adapters have separate entry points; importing lifecycle or transport contracts does
not load database or Kubernetes drivers.

A `DataSource` identifies a reusable client and constructs it. The `Client` owns initialization
and idempotent disposal; a `ClientManager` joins concurrent initialization for the same key and
closes owned clients when the caller finishes. A `ConnectionSource` resolves connection settings
and available `Transport` paths. Clients execute protocol operations through those paths.

```ts
import { ClientManager, dataSourceKey, type DataSource } from "@compforge/harness-toolbox";
import { MysqlClient } from "@compforge/harness-toolbox/mysql";
import { DirectTransport } from "@compforge/harness-toolbox/transport";

const target = { host: "localhost", port: 3306, database: "app", user: "reader", password: "..." };
const source: DataSource<MysqlClient> = {
  key: dataSourceKey("mysql", target),
  createClient: signal => new MysqlClient({
    resolve: async () => target,
    transports: [new DirectTransport()],
  }, { signal, connectTimeoutMs: 5_000, queryTimeoutMs: 10_000 }),
};
const clients = new ClientManager();
try {
  const db = await clients.get(source);
  const rows = await db.database.query(db.target, "SELECT 1 AS value", []);
} finally {
  await clients.dispose();
}
```

Callers own credentials, authorization, query semantics, capacity and timeouts. MySQL can use
TCP or a Python-capable Pod; the latter needs PyMySQL. Transport fallback occurs only when
establishing a connection, never by replaying a failed SQL operation. Pod inputs use stdin.

Pod log capture shares an identical Pod/container instance and absolute time window once per
client, while consumers filter independently and retain their own raw copies. Supply an explicit
capture policy, shared concurrency pool and byte budget; the package does not choose product
limits. Temporary capture files live until client disposal.

See [toolbox contracts](../../../docs/toolbox.md) for ownership and cross-language semantics.
