"""Resolve access addresses within the declared environment, without probing services."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass

from harness_common import Environment, KubernetesEnvironment

from harness_toolbox.client import ClientProvider, data_source_key
from harness_toolbox.errors import ErrorKind, ToolboxError
from harness_toolbox.transport import Endpoint


class AddressResolutionError(ToolboxError):
    """An address could not be resolved in its declared environment."""


@dataclass(frozen=True)
class AddressPolicy:
    """Environment and ordered alternatives for one logical connection target.

    Short names in Kubernetes environments are Services in ``namespace``. Qualified
    Service names use ``name.namespace.svc`` (optionally a cluster DNS suffix).
    Two-label names are Services only when their namespace matches the explicit
    scope. Other domains use DNS; no arbitrary domain is guessed into a Service.
    """

    environment: Environment | None = None
    namespace: str = ""
    fallback_hosts: tuple[str, ...] = ()
    timeout_s: float = 10
    connection_pool_maxsize: int = 8

    @property
    def key(self) -> str:
        config = asdict(self)
        # Logical aliases must not fragment clients using the same physical access.
        config["environment"] = (
            {"kubeconfig": self.environment.kubeconfig, "context": self.environment.context}
            if isinstance(self.environment, KubernetesEnvironment)
            else None
        )
        return data_source_key("address-policy", config)


@dataclass(frozen=True)
class AddressCandidate:
    endpoint: Endpoint
    source: str
    error: AddressResolutionError | None = None


def _service_name(host: str, namespace: str) -> tuple[str, str] | None:
    parts = host.rstrip(".").split(".")
    if len(parts) == 1:
        if not namespace:
            raise ValueError("Kubernetes short Service names require an explicit namespace")
        return host, namespace
    if len(parts) >= 3 and parts[2] == "svc":
        return parts[0], parts[1]
    if len(parts) == 2 and parts[1] == namespace:
        return parts[0], namespace
    return None


async def _dns(host: str, port: int, timeout_s: float) -> tuple[str, ...]:
    async with asyncio.timeout(timeout_s):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # Keep resolver preference (including IPv6), but remove duplicate socket records.
    return tuple(dict.fromkeys(record[4][0] for record in records))


async def _hosts(
    policy: AddressPolicy, endpoint: Endpoint, clients: ClientProvider | None
) -> tuple[tuple[str, ...], str]:
    try:
        ipaddress.ip_address(endpoint.host)
    except ValueError:
        pass
    else:
        return (endpoint.host,), "ip"
    environment = policy.environment
    service = (
        _service_name(endpoint.host, policy.namespace)
        if isinstance(environment, KubernetesEnvironment)
        else None
    )
    if service is None:
        return await _dns(endpoint.host, endpoint.port, policy.timeout_s), "dns"
    assert isinstance(environment, KubernetesEnvironment)
    if clients is None:
        raise ValueError("Kubernetes address resolution requires a ClientProvider")
    from harness_toolbox.environment import kubernetes_source
    from harness_toolbox.kube import Options

    name, namespace = service
    from aiohttp import ClientError
    from kubernetes_asyncio.config.config_exception import ConfigException

    try:
        kube = await clients.get(
            kubernetes_source(
                environment,
                Options(
                    namespace=namespace,
                    request_timeout_s=policy.timeout_s,
                    connection_pool_maxsize=policy.connection_pool_maxsize,
                ),
            )
        )
        # why: local short-name DNS can resolve a healthy database in a different cluster.
        # Service identity is authoritative; never fall back to ambient DNS for this name.
        return await kube.service_addresses(name), "kubernetes-service"
    except (ClientError, ConfigException) as error:
        raise AddressResolutionError(
            f"Cannot access Kubernetes environment {environment.name!r} for Service {name!r}",
            kind=ErrorKind.CONNECTION_FAILED,
        ) from error


async def address_candidates(
    endpoint: Endpoint, policy: AddressPolicy | None, clients: ClientProvider | None
) -> AsyncIterator[AddressCandidate]:
    """Yield ordered candidates; resolution failures remain visible before alternatives.

    Without a policy, transports keep ownership of name resolution (e.g. a remote
    Pod or a caller-supplied tunnel). No network work occurs while creating a source.
    """
    if policy is None:
        yield AddressCandidate(endpoint, "configured")
        return
    if policy.timeout_s <= 0 or policy.connection_pool_maxsize < 1:
        raise ValueError("Address resolution limits must be positive")
    seen: set[Endpoint] = set()
    for host in dict.fromkeys((endpoint.host, *policy.fallback_hosts)):
        configured = Endpoint(host, endpoint.port, endpoint.servername or endpoint.host)
        try:
            hosts, source = await _hosts(policy, configured, clients)
            if not hosts:
                raise AddressResolutionError(
                    f"No addresses for {host!r}", kind=ErrorKind.RESOURCE_NOT_FOUND
                )
        except (OSError, TimeoutError, ToolboxError) as error:
            kind = (
                error.kind
                if isinstance(error, ToolboxError)
                else (
                    ErrorKind.TIMEOUT
                    if isinstance(error, TimeoutError)
                    else ErrorKind.CONNECTION_FAILED
                )
            )
            failure = AddressResolutionError(
                f"Resolve {host!r} in environment "
                f"{policy.environment.name if policy.environment else 'local'!r}: {kind.value}",
                kind=kind,
                code=error.code if isinstance(error, ToolboxError) else None,
            )
            failure.__cause__ = error
            yield AddressCandidate(configured, "resolution", failure)
            continue
        for address in hosts:
            resolved = Endpoint(address, endpoint.port, configured.servername)
            if resolved not in seen:
                seen.add(resolved)
                yield AddressCandidate(resolved, source)
