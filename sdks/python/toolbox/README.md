# Harness Toolbox for Python

Async infrastructure clients for test runners, diagnostic commands, and operational Skills.
The same execution can create test Pods, inspect their state, run commands, query databases,
and collect logs without rebuilding clients for each nested operation.

Install only the protocols you use:

```sh
pip install 'harness-toolbox[kube,opensearch,mysql,prometheus]'
```

`harness-common` owns `Client`, `ClientProvider`, `DataSource` and `ClientManager`;
toolbox re-exports these names. `Client` owns initialization and disposal. A `ClientProvider` identifies its configuration;
`ClientManager` shares it across concurrent or nested work and cleans up at root exit.
`ConnectionSource` and `Transport` separate caller-owned configuration from the path used to reach it.

```python
import asyncio
from harness_toolbox import ClientManager
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.kube import Options

async def main():
    environment = KubernetesEnvironment(
        name="quality",
        options=Options(namespace="quality", request_timeout_s=15, connection_pool_maxsize=4),
        kubeconfig="/path/to/kubeconfig",
    )
    async with ClientManager() as clients:
        kube = await clients.get(environment)
        pods = await kube.list("v1", "Pod", label_selector="app=toolbox-demo")
        print(pods)

asyncio.run(main())
```

Use manifest create/get/list/delete and bounded Pod logs on the same environment client.
Consumers own created resources and must explicitly delete them; closing the client only releases access.

The caller chooses cluster, namespace, credentials, SQL, indexes, collection windows, and authorization.
The package does not read product-specific environment variables or registries. Kubernetes control uses
`kubernetes-asyncio`; exec, port-forward and log streams require `kubectl` on the host and use the same
kubeconfig/context. Pod Python access additionally requires Python and the protocol driver in the target Pod.

See [lifecycle and connection examples](docs/lifecycle.md) for shared clients, database routes, TLS,
log budgets, and cancellation behavior.

`resolve_workload` resolves explicit Kubernetes workloads by resource, Service selector or labels;
the caller supplies the stable environment ID for evidence.

Prometheus observations use `PrometheusDataSource` from `harness_toolbox.prometheus`
(extra `prometheus`). Its client scrapes a `/metrics` endpoint and evaluates PromQL locally
with Prombed; it does not query a remote Prometheus server. `ClientManager` owns the HTTP
pool and bounded history. Pass a `DataLoader` from `harness_toolbox.data_loader` to
`client.read(expressions, scope=scope)` to share one scrape across callers in that scope.
A new scope reads again; query results retain their Prometheus types and labels.
Declare `targets` for multiple instances or use `KubernetesScrapeDiscovery` with declared workloads.
Pod UID keeps replacement counters separate; HTTP targets must be reachable from the scraper.
`query_window(expressions, start_ms=..., end_ms=...)` queries retained local history without I/O,
rejecting incomplete scrapes or evicted history.

For a Prometheus server, use `PrometheusQueryDataSource` from
`harness_toolbox.prometheus_query` (extra `http`). It queries the HTTP API without
running a scraper or a local TSDB:

```python
from harness_common import ClientManager
from harness_toolbox.prometheus_query import PrometheusQueryDataSource

async with ClientManager() as clients:
    source = PrometheusQueryDataSource("https://prometheus.example/prometheus")
    client = await clients.get(source)
    results = await client.read(['sum(rate(http_requests_total{job="api"}[1m]))'])
    result = results['sum(rate(http_requests_total{job="api"}[1m]))']
    # result.data retains native types/labels; result.warnings and result.infos
    # retain server annotations for the caller to assess.
```

The URL is the server base URL, including any reverse-proxy prefix. Authentication
uses explicit `headers`. `PrometheusQueryOptions` bounds total query time, response
bytes, series count and pool size. Queries use one evaluation timestamp per call;
passing a `DataLoader` shares that timestamp and identical queries across readers
in one observation cycle. `timestamp=` selects an explicit Unix time in seconds.
Remote history is server-owned: a range selector such as `[1m]` can include data
from before the experiment. This client uses `/api/v1/query`; historical range
export via `/api/v1/query_range` is not part of this interface.

## Run-owned connections without a VPN

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
