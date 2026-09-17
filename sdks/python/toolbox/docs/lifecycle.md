# Execution-scoped infrastructure

## Ownership and identity

One root operation owns a `ClientManager`. Nested commands receive a `ClientProvider`, reuse its
clients, and join their work before the root exits. A cancelled waiter does not cancel shared
initialization. Root disposal stops pending initialization, drains it, and disposes ready clients
in reverse dependency order. A failed initialization is cleaned before retry; failed cleanup keeps
that identity failed and is reported again at finalize. Disposal does not delete remote workloads.

The manager is local to one Python execution and event loop. It cannot share live connections across
separately launched scripts. Synchronous CLIs call `asyncio.run` at their outer boundary; domain
operations remain async and receive the provider explicitly.

A ClientFactory key must cover everything that changes reuse: target, protocol, credentials, configuration,
and capacity. ConnectionSource accepts a caller-supplied stable key because environment resolution is
external. Use `client_key` to hash those inputs. Do not key only by a friendly service name when
multiple clusters or credentials can coexist. Configuration and credentials remain fixed for a root
execution; key new configurations separately.

## Database and HTTP paths

```python
from dataclasses import asdict
from harness_toolbox import ClientManager, client_key
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget
from harness_toolbox.transport import ConnectionSource, DirectTransport

async def query(clients, settings):
    target = MySQLTarget(**settings)
    async def resolve():
        return target
    source = MySQLDataSource(ConnectionSource(
        client_key("db-config", asdict(target)), resolve, (DirectTransport(),)
    ))
    database = await clients.get(source)
    return await database.query("SELECT id FROM message WHERE id = %(id)s", {"id": "example"})
```

MySQL uses SQLAlchemy with asyncmy for direct and forwarded access. It selects a route while establishing
a connection, before user SQL. Authentication errors may advance to another declared address, but
do not retry the same address through another transport. Statement failures never cause replay. PodPythonTransport sends explicit connection settings and query parameters over stdin and uses
PyMySQL in the Pod; it shares the resolved route/client, but opens a remote DB connection per exec.
Statements use DBAPI `%(name)s` parameters on both paths. Queries are bounded by concurrency, timeout,
and row limit. A statement timeout does not prove the server rolled back; callers decide transaction
and retry semantics.

With `params=None`, SQL is passed literally, so `DATE_FORMAT(ts, '%Y-%m-%d')` needs no escaping.
Supplying a parameter mapping, including `{}`, enables DBAPI interpolation: bind values with
`%(name)s` and write literal percent signs as `%%`. Do not interpolate values in the caller.

`QueryResult.rows` and `mappings()` preserve native Python values on both paths, including
`Decimal`, `datetime`, `date`, `time`, `timedelta` and `bytes`. The Pod wire format preserves
types and precision for parameters and results; it does not require toolbox installed in the Pod.
Consumers choose their own JSON presentation rather than relying on transport-specific string conversion.
For row-returning statements, `affected_rows` is `-1` (not a record count); use `len(result.rows)`.
Empty results retain their columns. Non-row statements retain the driver's affected-row count.

OpenSearchDataSource uses an HTTP pool and bounded response decoding. TLS verification is enabled;
private CAs use `ca_file`, and an intentional insecure environment must explicitly set
`insecure_skip_verify`. Forwarded connections preserve the original TLS server name. `scroll` yields
pages; use `contextlib.aclosing` when stopping early so the cursor is cleared before the client closes.
Consumers own index names, queries, and output files.

### Failures and connection evidence

Catch `ToolboxError` for translated access failures, or a protocol/operation subclass:
`MySQLError → MySQLConnectionError / MySQLQueryError` and
`OpenSearchError → OpenSearchConnectionError / OpenSearchRequestError`.

Every toolbox exception has three fields: `kind` is a stable `ErrorKind`, `code` is the optional
native DB/HTTP error code, and `message` is a safe contextual explanation, also returned by
`str(error)`. Interpret codes in the context of the protocol class, never by parsing messages.
Protocol adapters translate native failures once and preserve the original exception with
`raise ... from error`; already-translated exceptions propagate unchanged.

```python
from harness_toolbox import ToolboxError, ErrorKind

try:
    database = await clients.get(source)
    result = await database.query("SELECT 1")
except ToolboxError as error:
    if error.kind == ErrorKind.AUTHENTICATION_FAILED:
        handle_authentication_failure(error.message)
    report({"kind": error.kind, "code": error.code, "message": error.message})
else:
    report({"connection": database.diagnostics, "rows": len(result.rows)})
```

Consumers choose report fields and serialization. Exception messages exclude native driver text,
SQL/parameters, credentials and URL paths/query strings. Native causes and full tracebacks are
debugging material, **not** safe report output. Pod Python carries only safe error categories and
numeric MySQL codes; the host reconstructs the same public exception hierarchy without fabricating
an in-process driver cause.

`client.diagnostics` is independent connection evidence: protocol, resolved target, ordered
attempts and selected transport (`direct`, `port-forward` or `pod-python`). Snapshots are detached.
A target without a selected transport does not prove connection success. MySQL targets also expose
`target.diagnostics` before connecting. When initialization fails before a caller obtains the
client, the exception message still identifies the target and failed action; exceptions do not
carry the connection snapshot.

Arguments/programming errors and cancellation are not translated. Connection configuration
resolution belongs to the caller and its failures propagate unchanged. Result limits and invalid
remote responses are operational failures. Successful empty queries are not errors. Classification
does not authorize retries: only connection probes can use configured fallback routes; statements
and user requests are never automatically replayed.

## Kubernetes and logs

KubernetesClientFactory owns namespace, API capacity, exec capacity and timeouts. KubernetesClient offers
Pod create/get/list/delete, readiness/replacement/deletion waits, Event collection, exec, and
port-forward. Exec returns stdout, stderr and exit code separately. Nonzero process exit is a result;
connection failure, timeout, and output limit violations are exceptions. Created Pods are explicit
consumer-owned workloads, so tests use `try/finally` to delete their own physical Pod instance.

Delete uses a server-side UID precondition. Kubernetes does not support that condition for exec or
logs; the client checks identity before and after access and rejects a changed instance. This detects
replacement but cannot guarantee an atomic exec against a UID. Port-forward subprocesses belong to the
Kubernetes client and close at finalize.

PodLogDataSource depends on KubernetesClientFactory through the same provider. It owns one capture pool,
one total byte budget and a temporary directory. Physical Pod UID, container, restart count, previous
selection, and absolute timezone-aware `[since, until)` window identify a capture. Concurrent consumers
join the same capture; different trace IDs filter its file locally. Truncation is explicit in
PodLogCapture; timeout, transport failure or identity change do not publish a reusable capture.
Capture paths are borrowed and cease to exist after root disposal: copy evidence before root exit.

The capacities on a shared source apply to every user of that source. Use one configured log source
for all child commands rather than creating sources with conflicting limits. Defaults are bounded;
products should choose limits appropriate to their clusters.

## Common runtime identities

A logical Service declares its `workloads`; deployment configuration supplies the mapping. Workloads
are stable references, while Pod snapshots and UIDs are obtained during execution. The mapping can
change without changing the logical Service identity. Kubernetes Service endpoints are separate
from workload controllers and Pods.

```python
from harness_common import KubernetesWorkload
from harness_toolbox.environment import kubernetes_client_factory
from harness_toolbox.kube import Options

async def observe(ctx, service, environment_id):
    # ctx is an EnvironmentContext; the caller selected this operation's environment.
    factory = kubernetes_client_factory(ctx.environment, Options("default", ctx.remaining_s, 4))
    kube = await ctx.clients.get(factory)
    pods = []
    for workload in service.workloads:
        if not isinstance(workload, KubernetesWorkload):
            raise TypeError("This observer requires Kubernetes workloads")
        pods.extend(await kube.resolve_workload(workload, environment=environment_id))
    return pods
```

Workload operations are one toolbox capability, alongside database, OpenSearch, process and transport
operations. Those capabilities do not require Service or Workload objects. DataSource specializes
ClientFactory with data-access semantics; environment access factories are not data declarations.
EnvironmentContext borrows a provider and carries a deadline without owning disposal. ServiceDataSource can
associate logical context with independently configured sources without changing source keys.

resolve_workload uses the declaration's namespace or the client's explicit default, returns all
matching Pod incarnations, and raises KubernetesError for failed discovery rather than returning an
empty inventory. Catch ToolboxError.kind/code for reports; its native cause is debug-only.
The required environment ID comes from the caller's target registry, not the access path or a
display name. It remains unchanged when kubeconfig, credentials or access Host change for the same
target. The registry owns the binding between that ID and the selected access configuration.
For Environment.host access, use KubernetesResourcesClientFactory and its resolve_workload method;
it retains the same native/Host resource backend and ClientManager ownership.

PodPythonTransport accepts an explicit `container` for multi-container Pods. It is part of the
transport identity so clients using different containers cannot share a route. Credentials and
query parameters continue to travel over stdin.

## Environment-scoped address selection

Generic client ownership and service associations live in `harness_common`; toolbox
re-exports the original lifecycle names. `harness_toolbox.environment.kubernetes_client_factory`
is the Kubernetes-specific factory. The independent packages depend in one direction:
`quality-harness` → `harness-toolbox` → `harness-common`.

```python
from harness_common import KubernetesEnvironment
from harness_toolbox.address import AddressPolicy

addresses = AddressPolicy(
    environment=KubernetesEnvironment("smoke", "/configs/smoke", "smoke-context"),
    namespace="runtime",
    fallback_hosts=("mysql-replica.storage.svc",),
    timeout_s=10,
    connection_pool_maxsize=8,
)
# Pass addresses=addresses to ConnectionSource for MySQL or OpenSearch.
```

Resolution is lazy and shares Kubernetes clients through the root ClientProvider.
A Service lookup uses this environment's kubeconfig/context (empty kubeconfig means
in-cluster identity, never ambient local kubeconfig). A confirmed Service never falls
back to local short-name DNS. Cross-namespace names use `service.namespace.svc`;
ordinary domains retain DNS semantics. ClusterIP, ready headless addresses, external
DNS addresses and explicit alternatives are attempted sequentially and deduplicated.

Only initialization can switch addresses: authentication/database failures advance to
the next address, network failures may also advance to the next transport for the same
address. Queries are never replayed. The first successful client is reused; failed
attempt resources are closed. HTTPS keeps the configured SNI and HTTP Host even when
connecting by IP. Diagnostics distinguish configured target, candidate origin, mapped
endpoint and selected transport, without credentials or query data.
