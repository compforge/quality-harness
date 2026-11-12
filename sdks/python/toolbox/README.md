# Harness Toolbox for Python

Async infrastructure clients for test runners, diagnostic commands, and operational Skills.
The same execution can create test Pods, inspect their state, run commands, query databases,
and collect logs without rebuilding clients for each nested operation.

Install only the protocols you use:

```sh
pip install 'harness-toolbox[kube,opensearch,mysql,prometheus]'
```

For OpenAI-compatible model calls, install `harness-toolbox[llm]` and import
`LLMClient`, `LLMConfig` and `ChatResult` from `harness_toolbox.llm`. From 0.6 these
replace `harness_common.llm`; the client behavior is unchanged.

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

See [client lifecycle](docs/lifecycle.md) for shared clients, log budgets and cancellation, and
[connection transports](docs/transport.md) for address selection, database routes, HTTP and TLS.

`resolve_workload` resolves explicit Kubernetes workloads by resource, Service selector or labels;
the caller supplies the stable environment ID for evidence.

Prometheus observations use `MetricDataSource` from `harness_toolbox.metric`
(extra `prometheus`). Its client scrapes a `/metrics` endpoint and evaluates PromQL locally
with Prombed; it does not query a remote Prometheus server. `ClientManager` owns the HTTP
pool and bounded history. Pass a `DataLoader` from `harness_toolbox.data_loader` to
`client.read(expressions, scope=scope)` to share one scrape across callers in that scope.
A new scope reads again; query results retain their Prometheus types and labels.
Declare `targets` for explicit endpoints, or `environment` + `workloads` + `port` for live instances.
Set `transport="port-forward"` to reuse UID-bound Toolbox tunnels without a VPN.
Pod UID keeps replacement counters separate; HTTP targets must be reachable from the scraper.
`query_window(expressions, start_ms=..., end_ms=...)` queries retained local history without I/O,
rejecting incomplete scrapes or evicted history.

For a Prometheus server, use `PrometheusDataSource` from
`harness_toolbox.prometheus` (extra `http`). It queries the HTTP API without
running a scraper or a local TSDB:

```python
from harness_common import ClientManager
from harness_toolbox.prometheus import PrometheusDataSource

async with ClientManager() as clients:
    source = PrometheusDataSource("https://prometheus.example/prometheus")
    client = await clients.get(source)
    results = await client.read(['sum(rate(http_requests_total{job="api"}[1m]))'])
    result = results['sum(rate(http_requests_total{job="api"}[1m]))']
    # result.data retains native types/labels; result.warnings and result.infos
    # retain server annotations for the caller to assess.
```

The URL is the server base URL, including any reverse-proxy prefix. Authentication
uses explicit `headers`. `PrometheusOptions` bounds total query time, response
bytes, series count and pool size. Queries use one evaluation timestamp per call;
passing a `DataLoader` shares that timestamp and identical queries across readers
in one observation cycle. `timestamp=` selects an explicit Unix time in seconds.
Remote history is server-owned: a range selector such as `[1m]` can include data
from before the experiment. This client uses `/api/v1/query`; historical range
export via `/api/v1/query_range` is not part of this interface.

## Run-owned connections without a VPN

Choose [`Transport`](docs/transport.md) for direct access, UID-bound port-forward or a Workload-backed
Pod relay. `SocksProxy` and `HTTPClientProvider(transport=...)` connect HTTP clients to these paths
while preserving logical URLs, Host/TLS identity, pooling and streaming.

### Direct metrics through Workloads and port-forward

```python
from harness_common import ClientManager, KubernetesWorkload
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.metric import MetricDataSource, MetricOptions

source = MetricDataSource(
    environment=KubernetesEnvironment("qa", "/path/to/kubeconfig", context="qa-context"),
    workloads=(KubernetesWorkload("api", {"kind": "service", "name": "api"}, "quality"),),
    port=8001,
    transport="port-forward",
    metric_names=("api_requests_total", "api_request_duration_seconds"),
    options=MetricOptions(timeout_ms=15000, prepare_timeout_ms=30000),
)
async with ClientManager() as clients:
    metrics = await clients.get(source)
    result = await metrics.read(["sum(api_requests_total)"])
    evidence = metrics.diagnostics
```

Workload resolution is refreshed each cycle and shared within a DataLoader scope.
Targets retain Pod UID, independently of IP or forwarded local port. Tunnels are
prepared before the bounded scrape and reused by UID. HTTP pools keep their original
Host/TLS identity through the existing SOCKS adapter; neither proxy environment
variables nor kubevpn are required. Port-forward currently runs on the local
Kubernetes access host. It does not compress the payload.

`diagnostics` is a detached last-cycle snapshot with instance, transport, stage,
status, preparation/request/processing time and decoded/wire byte counts when known.
Failed instances retain their actual stage. Healthy samples remain stored after a
partial scrape, but reads/window queries reject incomplete fleet coverage.
Preparation and scrape deadlines are separate; request deadlines include queueing
and complete response consumption. The caller owns persistence of diagnostics.

API migration: direct scrapes use `MetricDataSource`, `MetricClient`, `MetricOptions`
from `harness_toolbox.metric`; Prometheus-compatible query APIs use
`PrometheusDataSource`, `PrometheusClient`, `PrometheusOptions` from
`harness_toolbox.prometheus`. No old-name aliases are provided.
