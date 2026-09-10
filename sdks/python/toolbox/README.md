# Harness Toolbox for Python

Async infrastructure clients for test runners, diagnostic commands, and operational Skills.
The same execution can create test Pods, inspect their state, run commands, query databases,
and collect logs without rebuilding clients for each nested operation.

Install only the protocols you use:

```sh
pip install 'harness-toolbox[kube,opensearch,mysql]'
```

`Client` owns initialization and disposal. A `DataSource` identifies its configuration;
`ClientManager` shares it across concurrent or nested work and cleans up at root exit.
`ConnectionSource` and `Transport` separate caller-owned configuration from the path used to reach it.

```python
import asyncio
from harness_toolbox import ClientManager
from harness_toolbox.kube import KubernetesDataSource, Options, PodSpec

async def main():
    source = KubernetesDataSource(
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

The caller chooses cluster, namespace, credentials, SQL, indexes, collection windows, and authorization.
The package does not read product-specific environment variables or registries. Kubernetes control uses
`kubernetes-asyncio`; exec, port-forward and log streams require `kubectl` on the host and use the same
kubeconfig/context. Pod Python access additionally requires Python and the protocol driver in the target Pod.

See [lifecycle and connection examples](docs/lifecycle.md) for shared clients, database routes, TLS,
log budgets, and cancellation behavior.
