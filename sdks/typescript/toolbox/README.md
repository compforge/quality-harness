# Harness Toolbox

Client, ClientFactory, DataSource, EnvironmentContext and ClientManager are owned by the sibling TypeScript package
`@compforge/harness-common`. Toolbox provides protocol implementations and transports;
its lifecycle entrypoints re-export the same common symbols.

Shared infrastructure for diagnostic tools, test runners and business adapters. Use the same
clients to access Kubernetes, MySQL, Redis, OpenSearch and S3-compatible object storage without depending on a particular
command, plugin protocol, test model or verdict.

The package supports Node.js 22+ and Bun. Install with `npm install @compforge/harness-toolbox`.
Protocol adapters have separate entry points; importing lifecycle or transport contracts does
not load database or Kubernetes drivers.

## Workload discovery

Borrow a KubernetesClient through KubernetesClientFactory and the shared ClientManager lifecycle, then call
`client.resolveWorkload(workload, environmentId)`. The ID is a stable target identity from your
environment registry, not a display name or kubeconfig path. The Workload's namespace overrides the client's default.
Resource, Service selector and labels locations return all matching Pod incarnations without
readiness filtering; successful empty discovery returns an empty array. Missing resources,
selectorless Services and failed access raise KubernetesError.

The resource path uses client-node kubeconfig authentication/TLS and bounded native JSON reads,
not kubectl error text. Configure request timeout, concurrency and response bytes with the
client's ResourceLimits constructor argument. Existing exec/port-forward operations remain unchanged.
See [the shared Workload model](../../../docs/workload.md).

```ts
import { ClientManager, EnvironmentContext } from "@compforge/harness-toolbox";
import { KubernetesClientFactory } from "@compforge/harness-toolbox/kubernetes/client-factory";

const clients = new ClientManager();
try {
  const ctx = new EnvironmentContext(
    { id: "cluster-a/runtime", kubeconfig: "/config/cluster", context: "dev", namespace: "app" },
    clients, performance.now() + 30_000,
  );
  const kube = await ctx.clients.get(new KubernetesClientFactory(ctx.environment, {
    timeoutMs: 10_000, concurrency: 4, maxBytes: 8 * 1024 * 1024,
  }));
  const pods = await kube.resolveWorkload({
    name: "api", platform: "kubernetes",
    location: { kind: "resource", resource_kind: "Deployment", name: "api" },
  }, ctx.environment.id);
} finally {
  await clients.dispose();
}
```

The context does not cancel work automatically. Its budget is available as remainingMs;
the root owns cancellation and must join child work before disposing clients.

Catch ToolboxError from the root entry or `@compforge/harness-toolbox/errors`; use `kind` and
`code` for decisions. Native `cause` may contain sensitive details and is debug-only.

For coordinated common/toolbox development, install and build common before installing toolbox:

```sh
# From sdks/typescript/toolbox
(cd ../common && bun install --frozen-lockfile && make build)
bun install --frozen-lockfile
make lint
make test
```

The toolbox root override resolves common from `../common` only for local development;
the published dependency remains a registry version. Publish common before toolbox.
The Makefile builds common before typechecking or building toolbox.

A `ClientFactory` identifies a reusable client and constructs it; `DataSource` adds data-access semantics.
Environment access factories need not pretend to be data sources. The `Client` owns initialization
and idempotent disposal; a `ClientManager` joins concurrent initialization for the same key and
closes owned clients when the caller finishes. A `ConnectionSource` resolves connection settings
and available `Transport` paths. Clients execute protocol operations through those paths.

```ts
import { ClientManager, clientKey, type DataSource } from "@compforge/harness-toolbox";
import { MysqlClient } from "@compforge/harness-toolbox/mysql";
import { DirectTransport } from "@compforge/harness-toolbox/transport";

const target = { host: "localhost", port: 3306, database: "app", user: "reader", password: "..." };
const source: DataSource<MysqlClient> = {
  key: clientKey("mysql", target),
  createClient: (_clients, signal) => new MysqlClient({
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

## Read S3 objects

S3 uses the official AWS SDK behind the same managed lifecycle. Supply resolved connection
settings and explicit limits; the package does not discover business configuration or credentials.

```ts
import { ClientManager } from "@compforge/harness-toolbox";
import { S3DataSource } from "@compforge/harness-toolbox/s3";

const clients = new ClientManager();
try {
  const s3 = await clients.get(new S3DataSource({
    endpoint: "https://s3.example.com", region: "us-east-1",
    credentials: { accessKeyId: "...", secretAccessKey: "..." },
  }, { concurrency: 4, connectTimeoutMs: 5_000, requestTimeoutMs: 15_000 }));
  const object = await s3.headObject("artifacts", "runs/example.json");
  const prefix = await s3.readObject("artifacts", "runs/example.json", { maxBytes: 65_536 });
  // prefix.truncated distinguishes a bounded prefix from the complete object.
} finally {
  await clients.dispose();
}
```

`listObjects` and `listBuckets` return one explicitly bounded page with continuation tokens;
callers own traversal and total scan budgets. Bucket HEAD and versioning are also available.
Errors preserve SDK status/code: a failed HEAD is not converted to `exists=false`. Reads stop at
the byte cap and close the body; request deadlines include queueing and body consumption.
No write operations, automatic retries or implicit region redirects are exposed.

For a mapped TCP transport, pass `{ key: "cluster/context identity", transport }` as the third
DataSource argument. The caller owns that transport's lifetime. Path-style addressing is required
for mapped routes; direct endpoints also support virtual-hosted buckets. Signed Host and TLS
identity remain the logical endpoint, not the forwarded address. Private CAs may be supplied
explicitly with `ca`; certificate verification stays enabled. Initialization prepares the client
and route without listing buckets or claiming that credentials have been validated.

## Bounded MySQL reads

`MysqlClient.database.queryReadonly(target, sql, values, { timeoutMs, maxRows, maxBytes })` uses
the shared query slot and a disposable native session with `NO_BACKSLASH_ESCAPES`, a SELECT deadline,
and a READ ONLY transaction. Prepared execution binds values and retains rows incrementally,
returning `rows`, `columns`, `bytes`, `truncated`, and `truncation`. An extra row proves row truncation;
a row exceeding the remaining byte budget is not retained.

Callers own SQL classification, function policy, and least-privilege identities: READ ONLY alone
is not an arbitrary-SQL sandbox. This API requires MySQL 5.7.8+ `max_execution_time` and does not
fall back to Pod Python. Completion, truncation, timeout, and cancellation destroy the disposable
session without replay or changes to ordinary borrowed sessions. Disconnect does not prove immediate
server cancellation. The byte budget limits retained JSON rows, not individual wire packets or fields.
