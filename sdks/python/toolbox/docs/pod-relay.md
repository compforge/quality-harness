# Pod relay Transport

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
