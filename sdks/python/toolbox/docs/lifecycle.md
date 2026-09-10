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

A DataSource key must cover everything that changes reuse: target, protocol, credentials, configuration,
and capacity. ConnectionSource accepts a caller-supplied stable key because environment resolution is
external. Use `data_source_key` to hash those inputs. Do not key only by a friendly service name when
multiple clusters or credentials can coexist. Configuration and credentials remain fixed for a root
execution; key new configurations separately.

## Database and HTTP paths

```python
from dataclasses import asdict
from harness_toolbox import ClientManager, data_source_key
from harness_toolbox.mysql import MySQLDataSource, MySQLTarget
from harness_toolbox.transport import ConnectionSource, DirectTransport

async def query(clients, settings):
    target = MySQLTarget(**settings)
    async def resolve():
        return target
    source = MySQLDataSource(ConnectionSource(
        data_source_key("db-config", asdict(target)), resolve, (DirectTransport(),)
    ))
    database = await clients.get(source)
    return await database.query("SELECT id FROM message WHERE id = %(id)s", {"id": "example"})
```

MySQL uses SQLAlchemy with asyncmy for direct and forwarded access. It selects a route while establishing
a connection, before user SQL. Authentication errors and statement failures do not cause fallback or
replay. PodPythonTransport sends explicit connection settings and query parameters over stdin and uses
PyMySQL in the Pod; it shares the resolved route/client, but opens a remote DB connection per exec.
Statements use DBAPI `%(name)s` parameters on both paths. Queries are bounded by concurrency, timeout,
and row limit. A statement timeout does not prove the server rolled back; callers decide transaction
and retry semantics.

OpenSearchDataSource uses an HTTP pool and bounded response decoding. TLS verification is enabled;
private CAs use `ca_file`, and an intentional insecure environment must explicitly set
`insecure_skip_verify`. Forwarded connections preserve the original TLS server name. `scroll` yields
pages; use `contextlib.aclosing` when stopping early so the cursor is cleared before the client closes.
Consumers own index names, queries, and output files.

## Kubernetes and logs

KubernetesDataSource owns namespace, API capacity, exec capacity and timeouts. KubernetesClient offers
Pod create/get/list/delete, readiness/replacement/deletion waits, Event collection, exec, and
port-forward. Exec returns stdout, stderr and exit code separately. Nonzero process exit is a result;
connection failure, timeout, and output limit violations are exceptions. Created Pods are explicit
consumer-owned workloads, so tests use `try/finally` to delete their own physical Pod instance.

Delete uses a server-side UID precondition. Kubernetes does not support that condition for exec or
logs; the client checks identity before and after access and rejects a changed instance. This detects
replacement but cannot guarantee an atomic exec against a UID. Port-forward subprocesses belong to the
Kubernetes client and close at finalize.

PodLogDataSource depends on KubernetesDataSource through the same provider. It owns one capture pool,
one total byte budget and a temporary directory. Physical Pod UID, container, restart count, previous
selection, and absolute timezone-aware `[since, until)` window identify a capture. Concurrent consumers
join the same capture; different trace IDs filter its file locally. Truncation is explicit in
PodLogCapture; timeout, transport failure or identity change do not publish a reusable capture.
Capture paths are borrowed and cease to exist after root disposal: copy evidence before root exit.

The capacities on a shared source apply to every user of that source. Use one configured log source
for all child commands rather than creating sources with conflicting limits. Defaults are bounded;
products should choose limits appropriate to their clusters.

## Common runtime identities

`Service` remains a platform-independent logical service. Deployment configuration can map it to
multiple workloads or none; no Kubernetes resource name is inferred from its name.

```python
from harness_common.toolbox import ServiceDataSource, kubernetes_source
from harness_toolbox.kube import Options
from harness_toolbox.transport import PortForwardTransport

async def observe(clients, service, environment, workload_names):
    source = kubernetes_source(environment, Options("runtime", 15, 4))
    access = ServiceDataSource(service, source)
    kube = await clients.get(access.source)
    pods = []
    for name in workload_names:  # explicit deployment configuration
        pods.extend(await kube.list_deployment_pods(name))
    route = PortForwardTransport(kube.access, "service/shared-storage", 9200)
    return pods, route
```

A Service may have multiple typed DataSource associations, and multiple Services may share one
source. The association supplies logical context without changing the underlying source key.
The route can be supplied to a protocol's ConnectionSource within the same root execution;
protocol initialization opens the tunnel and protocol disposal closes it.
