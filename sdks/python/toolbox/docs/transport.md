# Connection transports

A protocol client owns HTTP, database operations and their pools. A `Transport` prepares the path
used to reach a logical endpoint. Connection mapping preserves protocol identity: changing a socket
address must not change HTTP Host, TLS server name, credentials or request content.

The root [ClientManager](lifecycle.md#ownership-and-identity) owns shared clients and their borrowed
transports. Clients close first. Transport failure may invalidate a channel; it never authorizes
replaying a business operation. Constructors and providers describe configuration without I/O.

## Available paths

| Implementation | Connection path | Selection boundary |
| --- | --- | --- |
| `DirectTransport` | Local socket to the logical endpoint | Endpoint must be reachable from the caller |
| `PortForwardTransport` | Owned `kubectl port-forward` to one resource/port | A forwarded Service selects one Pod |
| `KubernetesPortForwardTransport` | Shared, UID-checked tunnels for registered Service endpoints or live Pod IPs | One explicit namespace |
| `PodRelayTransport` | Owned port-forward to an existing Pod helper, then TCP to the logical endpoint | Explicit Workload; a Service ClusterIP retains backend selection |
| `PodPythonTransport` | Execute the protocol adapter's Python script inside a Pod | Protocol-specific execution, rather than a general TCP tunnel |

`ConnectionSource` supplies database/OpenSearch clients with a configured target, lazy resolver and
ordered routes. Its caller-supplied stable key covers configuration because resolution is external.
`HTTPClientProvider` accepts a borrowed `ClientProvider[HTTPRoute]`; the lifecycle-aware
`KubernetesPortForwardTransport` and `PodRelayTransport` implement that contract. A standalone
`PortForwardTransport` is used as an explicit async connection scope.

## ConnectionSource and protocol clients

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

PodPythonTransport accepts an explicit `container` for multi-container Pods. It is part of the
transport identity so clients using different containers cannot share a route. Credentials and
query parameters continue to travel over stdin.

## Environment-scoped address selection

Generic client ownership and service associations live in `harness_common`; toolbox
re-exports the lifecycle names. `harness_toolbox.environment.KubernetesEnvironment`
is the concrete environment provider. The independent packages depend in one direction:
`quality-harness` → `harness-toolbox` → `harness-common`.

```python
from harness_toolbox.environment import KubernetesEnvironment
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

Resolution is lazy and shares Kubernetes clients through the root ClientManager.
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

## Port-forward and SOCKS

`KubernetesPortForwardTransport` resolves a selected Service and dynamic Pod IPs
within one explicit namespace, reusing `PortForwardTransport` to own local tunnels.
`SocksProxy` exposes any Python `Transport` to an external HTTP client through a
loopback-only SOCKS5 endpoint. It preserves request URLs, Host/SNI, credentials and
keep-alive bytes, and never retries business requests or falls back to direct routing.

```python
from harness_toolbox.kube_portforward import KubernetesPortForwardTransport
from harness_toolbox.socks import SocksProxy
from harness_toolbox.transport import KubernetesAccess

async with KubernetesPortForwardTransport(
    KubernetesAccess("/path/to/kubeconfig", "quality")
) as transport:
    target = await transport.service_endpoint("api", 8080)
    async with SocksProxy(transport) as proxy:
        child_env_overrides = proxy.environment()
        # Run a prepared client against target.host:target.port with these overrides.
        # Await/stop the client before leaving the proxy scope.
```

Requires `kubectl`, permissions to get/list the selected resources and open their
port-forwards, and a client that honors SOCKS proxy configuration. Only explicitly
registered Service endpoints or live non-host-network Pod IPs in the chosen namespace
are accepted; arbitrary external destinations are rejected. This is not a reverse
tunnel for workload-to-runner fixture servers or a test of ingress/DNS reachability.
Tunnel identity includes resource UID; startup verifies that the target was not
replaced. Port-forward is not a Kubernetes API with atomic UID preconditions.

The default proxy limit is 128 active connections with a 30-second setup deadline;
client/workload timeouts bound established operations. No product registry, business
credential source or health endpoint is built in. For a project-owned E2E command,
`e2e_harness.command.run_command` owns the connection/proxy/process scope and returns
the command's native exit code; project readiness and fixture cleanup remain separate.

## Pod relay

`PodRelayTransport` owns a leased TCP helper in an explicitly declared existing Kubernetes
Workload. It reuses Workload discovery and the root `ClientManager`; it creates no Pod or Service.
Install `harness-toolbox[http,kube]`, provide local kubeconfig/context and `kubectl`, and select a
Running Pod with a Ready container containing Python >= 3.8. Workload `namespace` overrides the
Environment default; `container` selects the executable container. Candidate selection is bounded.
Remote SSH Environment access is not supported by this transport.

```python
from harness_common import ClientManager, KubernetesWorkload
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.http import HTTPClientProvider
from harness_toolbox.pod_relay import PodRelayTransport

route = PodRelayTransport(
    KubernetesEnvironment("qa", "/path/to/kubeconfig", context="qa-context"),
    KubernetesWorkload("relay", {"kind": "service", "name": "utility"}, "quality"),
    max_connections=128,
)

async def inspect():
    async with ClientManager() as clients:
        http = await clients.get(HTTPClientProvider(100, 120, transport=route))
        relay = await clients.get(route)
        response = await http.get("http://10.0.0.10:8001/health")
        response.raise_for_status()
        return relay.diagnostics
```

The path is `HTTP client → loopback SOCKS → owned kubectl port-forward → helper → logical TCP
endpoint`. Request headers, TLS and streaming bytes are handled by the original protocol client.
Cluster DNS resolves inside the relay Pod. A Service ClusterIP stays a Service destination;
Kubernetes selects backends for the helper's new connections. HTTP keep-alive may reuse a selected
backend; this does not promise equal per-request distribution. Forwarding the Service itself would
select one Pod and would not test the same path.

Connection and preparation budgets are independent of the HTTP request timeout. Defaults:
startup 30s (including discovery/helper initialization), upstream connect 10s, 128 concurrent relay
connections, 16 targets, 3 candidate Pods, heartbeat 5s, lease 30s. New targets use the startup budget.
The HTTP adapter's SOCKS setup budget is 30s. Set relay `max_connections` at least as high as the
HTTP pool; it bounds simultaneously open TCP sockets, including idle keep-alive connections.

Only initialization probes may try another Python executable/candidate. The selected Pod UID is
checked before and after helper startup and new tunnel preparation; Kubernetes exec/port-forward
has no atomic UID precondition. Target listeners/tunnels are reused by logical host/port. A failed
control channel or target preparation retires the session and closes known tunnels. It never
falls back to a local route or replays business requests. Create a new root scope to reselect a relay.

HTTP clients close before the borrowed relay. Normal shutdown sends EOF, closes active connections
and listeners, and reaps owned local processes. On a broken exec channel the helper expires after
`lease_s` without control traffic; this bounds orphan lifetime rather than proving immediate remote
cleanup. Suspend/overload of the generator can also expire the lease. Diagnostics expose selected
Pod UID, container, helper PID, targets and terminal failure; retain this snapshot with run evidence.
A relay consumes CPU/memory in the selected Pod, and port-forward/API-server bandwidth can itself
limit a load generator. Record the relay carrier and observe it when interpreting a load run.
