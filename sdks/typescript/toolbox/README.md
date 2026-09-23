# Harness Toolbox

Client, ClientProvider, DataSource, EnvironmentContext and ClientManager are owned by the sibling TypeScript package
`@compforge/harness-common`. Toolbox provides protocol implementations and transports;
its lifecycle entrypoints re-export the same common symbols.

Shared infrastructure for diagnostic tools, test runners and business adapters. Use the same
clients to access Kubernetes, MySQL, Redis, OpenSearch and S3-compatible object storage without depending on a particular
command, plugin protocol, test model or verdict.

The package supports Node.js 22+ and Bun. Install with `npm install @compforge/harness-toolbox`.
Use `@compforge/harness-toolbox/duration` to parse a positive duration such as `30m` or `2d` into milliseconds.
`searchAfterPages` from `@compforge/harness-toolbox/opensearch/search-after` yields raw OpenSearch
hit pages with a caller supplied query and stable sort. It streams pages without owning the client or
claiming snapshot consistency; callers supply cancellation and decide how to persist or verify results.
Protocol adapters have separate entry points; importing lifecycle or transport contracts does
not load database or Kubernetes drivers.

## Workload discovery

Borrow a KubernetesClient through KubernetesEnvironment and the shared ClientManager lifecycle, then call
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
import { KubernetesEnvironment } from "@compforge/harness-toolbox/kubernetes/environment";

const clients = new ClientManager();
try {
  const ctx = new EnvironmentContext(
    new KubernetesEnvironment("dev",
      { kubeconfig: "/config/cluster", context: "dev", namespace: "app" },
      { timeoutMs: 10_000, concurrency: 4, maxBytes: 8 * 1024 * 1024 }),
    clients, performance.now() + 30_000,
  );
  const kube = await ctx.clients.get(ctx.environment);
  const pods = await kube.resolveWorkload({
    name: "api", platform: "kubernetes",
    location: { kind: "resource", resource_kind: "Deployment", name: "api" },
  }, "cluster-a/runtime");
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

A `ClientProvider` identifies a reusable client and constructs it; `DataSource` adds data-access semantics.
Accessible environments implement ClientProvider directly without becoming data sources. The `Client` owns initialization
and idempotent disposal; a `ClientManager` joins concurrent initialization for the same key and
closes owned clients when the caller finishes. A `ConnectionSource` resolves connection settings
and available `Transport` paths. Clients execute protocol operations through those paths.

```ts
import { ClientManager, clientKey, type DataSource } from "@compforge/harness-toolbox";
import { MysqlClient } from "@compforge/harness-toolbox/mysql";
import { DirectTransport } from "@compforge/harness-toolbox/transport";

const target = { host: "localhost", port: 3306, database: "app", user: "reader", password: "..." };
const source: DataSource<MysqlClient> = {
  clientKey: clientKey("mysql", target),
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

## Reusable bounded queries and read caches

For trusted, caller-owned SELECT statements, `queryLimited(database, target, statement, maxRows)`
appends an integer `LIMIT maxRows + 1` and returns `{ rows, truncated }`. SQL must not already have
LIMIT or a trailing terminator; the helper does not parse or rewrite arbitrary SQL. Business values
stay bound parameters. `limitedStatement` and `limitedRows` expose the same preparation/projection
for `Database.queryBatch`. `maxRows` must be a positive integer below 2147483647. These helpers
bound row count only: use `queryReadonly` when a disposable readonly session and byte/time budgets
are required.

`DataLoader` shares results, failures and in-flight work within one explicit read scope, similar to
Guava's LoadingCache but scoped to a single collection round. Consumers must not mutate shared results.
Use `withReadScope` to bind caller-approved database reads; do not use it for writes or polling.

```ts
import { DataLoader } from '@compforge/harness-toolbox/data-loader';
import { withReadScope, queryLimited } from '@compforge/harness-toolbox/mysql';

const scope = new DataLoader({
  maxEntries: 256, maxBytes: 4 * 1024 * 1024, signal: client.signal,
});
const reads = withReadScope(client.database, scope);
try {
  const page = await queryLimited(reads, client.target, {
    sql: 'SELECT id FROM items WHERE owner = ? ORDER BY id', values: [owner],
  }, 100);
} finally {
  await scope.close(); // Root still owns client.dispose().
}
```

Keys include full database identity, SQL and typed parameters as an opaque digest. Nonserializable
parameters bypass caching. Batch reads reuse cached entries and send misses as one ordered native
batch, or sequential queries when batching is unavailable. Failures are shared without replay;
use a new scope for fresh observations. This is not a consistent database snapshot.

Entry capacity includes failures and pending reads; new keys bypass caching when full. Serialized
result bytes bound retained results, excluding keys/errors and active operation memory. Oversized
or nonserializable results are returned but not retained. Scope close signals cancellation and joins
operations, including uncached reads. Operations must honor the signal for prompt cancellation;
MySQL reads retain their underlying client's timeout/cancellation policy. Adapter close owns neither
the scope nor the database. A waiter's optional signal to `scope.read` cancels only that waiter.

## Shared Pod relay transport

`PodRelayTransport` opens TCP routes through an existing Running/Ready Pod. It discovers an
eligible container in the selected namespace (optionally restricted by a label selector); the
current implementation uses Python 3.8+ standard-library asyncio. Callers supply endpoints,
not a Pod name or interpreter. It creates no Pods and installs no packages. The selected Pod
must be able to resolve and reach each destination.

Create one transport in the root execution's ClientManager and lend it to all Service/protocol
clients. Its key describes cluster access and relay policy, never a Service or database identity.
Concurrent callers share Pod discovery, one relay process and one forward per host/port. Database
credentials and connections remain owned by their protocol clients.

```ts
import { ClientManager } from '@compforge/harness-toolbox';
import { PodRelayTransport } from '@compforge/harness-toolbox/transport';

const clients = new ClientManager();
try {
  const relay = await clients.get({
    clientKey: 'transport:customer-cluster:app',
    createClient: (_clients, signal) => new PodRelayTransport({
      kubeconfig: '/config/cluster', context: 'customer', namespace: 'app',
      selector: 'diagnostic-relay=eligible',
      startupTimeoutMs: 5000, connectTimeoutMs: 5000,
      maxConnections: 16, maxTargets: 8, maxCandidatePods: 8, signal,
    }),
  });
  // The same instance belongs in each protocol client's ConnectionSource.transports.
  const address = await relay.connect({host: 'database.internal', port: 3306});
  // Connect the native protocol client to address; do not dispose relay in a Service.
} finally {
  await clients.dispose();
}
```

The host must authorize `list pods`, `create pods/exec` and `create pods/portforward`, including
running a temporary process in the selected container. List/discovery and each readiness operation
use startupTimeoutMs; candidate Pod count, target count and aggregate live relay connections are
bounded separately. List pods is namespace scoped. Relay listeners bind only to Pod loopback and
local forwards only to host loopback. Control messages contain destinations, never DB credentials.

Disposal closes forwards and the exec stream; EOF closes remote listeners/connections. If an exec
connection is lost without EOF, a 30-second heartbeat lease bounds remote process lifetime.
A dead relay invalidates its routes and fails callers; no SQL or other protocol operation is replayed.
The root should finish or cancel protocol work before disposing the transport.
