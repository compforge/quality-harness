# Harness Toolbox for Python

Async infrastructure clients for test runners, diagnostic commands, and operational Skills.
The same execution can create test Pods, inspect their state, run commands, query databases,
and collect logs without rebuilding clients for each nested operation.

Install only the protocols you use:

```sh
pip install 'harness-toolbox[kube,opensearch,mysql,prometheus]'
```

`harness-common` owns `Client`, `ClientFactory`, `DataSource`, `ClientProvider` and `ClientManager`;
toolbox re-exports these names. `Client` owns initialization and disposal. A `ClientFactory` identifies its configuration;
`ClientManager` shares it across concurrent or nested work and cleans up at root exit.
`ConnectionSource` and `Transport` separate caller-owned configuration from the path used to reach it.

```python
import asyncio
from harness_toolbox import ClientManager
from harness_toolbox.kube import KubernetesClientFactory, Options, PodSpec

async def main():
    source = KubernetesClientFactory(
        options=Options(namespace="quality", request_timeout_s=15, connection_pool_maxsize=4),
        kubeconfig="/path/to/kubeconfig",
    )
    async with ClientManager() as clients:
        kube = await clients.get(source)
        pod = await kube.create_pod(PodSpec("toolbox-demo", "busybox:1.37",
                                              command=("sleep", "300")))
        try:
            await kube.wait_ready(pod.ref(), timeout_s=60, interval_s=1)
            result = await kube.execute(pod.ref(), ["echo", "hello"])
            print(result.stdout.decode(), result.exit_code)
        finally:
            await kube.delete_pod(pod.ref())
            await kube.wait_deleted(pod.ref(), timeout_s=60, interval_s=1)

asyncio.run(main())
```

Existing workloads can be observed with `kube.list_service_pods(name)` or
`kube.list_deployment_pods(name)`. Both use the resource's label selector, return stable Pod
observations, and leave readiness/sample selection to the caller. Missing resources raise
`ResourceNotFoundError`; permission and network errors remain failures. Selectorless resources
cannot be used to enumerate all Pods accidentally.

The caller chooses cluster, namespace, credentials, SQL, indexes, collection windows, and authorization.
The package does not read product-specific environment variables or registries. Kubernetes control uses
`kubernetes-asyncio`; exec, port-forward and log streams require `kubectl` on the host and use the same
kubeconfig/context. Pod Python access additionally requires Python and the protocol driver in the target Pod.

See [lifecycle and connection examples](docs/lifecycle.md) for shared clients, database routes, TLS,
log budgets, and cancellation behavior.

`list_workload_pods` resolves Deployment, StatefulSet, DaemonSet or an explicit Pod.
Kubernetes Service endpoints retain their separate `list_service_pods` operation.

Prometheus observations use `PrometheusDataSource` from `harness_toolbox.prometheus`
(extra `prometheus`). Its client scrapes a `/metrics` endpoint and evaluates PromQL locally
with Prombed; it does not query a remote Prometheus server. `ClientManager` owns the HTTP
pool and bounded history. Pass a `DataLoader` from `harness_toolbox.data_loader` to
`client.read(expressions, scope=scope)` to share one scrape across callers in that scope.
A new scope reads again; query results retain their Prometheus types and labels.

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
