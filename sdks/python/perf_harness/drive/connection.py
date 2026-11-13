"""Explicit load-request connection selection; no cluster discovery in the Engine."""

from dataclasses import dataclass, replace

from harness_common import ClientProvider, KubernetesWorkload
from harness_toolbox.environment import KubernetesEnvironment
from harness_toolbox.http import HTTPRoute
from harness_toolbox.transport import PodRelayTransport
from harness_toolbox.transport.pod_relay import DEFAULT_MAX_CONNECTIONS

from perf_harness.model import Service


def parse_request_transport(value: object, service: Service) -> ClientProvider[HTTPRoute] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("request_transport must be a mapping")
    if value == {"kind": "direct"}:
        return None
    if value.get("kind") != "pod-relay":
        raise ValueError("request_transport.kind must be direct or pod-relay")
    allowed = {
        "kind",
        "workload",
        "startup_timeout_s",
        "connect_timeout_s",
        "max_connections",
        "max_targets",
        "max_candidate_pods",
        "heartbeat_s",
        "lease_s",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown request_transport fields: {', '.join(sorted(unknown))}")
    if not isinstance(service.environment, KubernetesEnvironment):
        raise ValueError("pod-relay requires a Kubernetes service environment")
    if not isinstance(value.get("workload"), dict):
        raise ValueError("pod-relay requires an explicit Kubernetes workload")
    options = {k: v for k, v in value.items() if k not in {"kind", "workload"}}
    transport = PodRelayTransport(
        service.environment, KubernetesWorkload(**value["workload"]), **options
    )
    return transport


@dataclass(frozen=True)
class EffectiveConnection:
    """Resolved once by Perf; all load-path connection pools consume this budget."""

    max_connections: int
    transport: ClientProvider[HTTPRoute] | None
    configured_max_connections: int | None


def resolve_connection(
    transport: ClientProvider[HTTPRoute] | None, peak_inflight: int
) -> EffectiveConnection:
    """@spec HTTP pool, local proxy and Pod relay use the same effective capacity.

    @why Users configure load concurrency; internal defaults must not reject that load.
    Observation connections and request timeouts remain independent.
    """
    configured = transport.max_connections if isinstance(transport, PodRelayTransport) else None
    effective = max(DEFAULT_MAX_CONNECTIONS, configured or 0, peak_inflight)
    resolved = (
        replace(transport, max_connections=effective)
        if isinstance(transport, PodRelayTransport)
        else transport
    )
    return EffectiveConnection(effective, resolved, configured)
