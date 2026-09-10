"""Async namespace-scoped Kubernetes driver."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from kubernetes_asyncio import client as kubernetes
from kubernetes_asyncio import config
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.client import ClientProvider, data_source_key
from harness_toolbox.kube.model import (
    Container,
    Event,
    Options,
    Pod,
    PodRef,
    PodSpec,
    ResourceNotFoundError,
)
from harness_toolbox.kube.selector import label_selector
from harness_toolbox.process import ExecResult, execute
from harness_toolbox.transport import Endpoint, KubernetesAccess, PortForwardTransport

T = TypeVar("T")


class _CoreV1API(Protocol):
    async def read_namespaced_service(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Service: ...

    async def create_namespaced_pod(self, namespace: str, **kwargs: Any) -> kubernetes.V1Pod: ...

    async def list_namespaced_pod(self, namespace: str, **kwargs: Any) -> kubernetes.V1PodList: ...

    async def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Pod: ...

    async def delete_namespaced_pod(self, name: str, namespace: str, **kwargs: Any) -> Any: ...

    async def list_namespaced_event(
        self, namespace: str, **kwargs: Any
    ) -> kubernetes.CoreV1EventList: ...


class _AppsV1API(Protocol):
    async def read_namespaced_deployment(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Deployment: ...


@dataclass(frozen=True)
class KubernetesDataSource:
    options: Options
    kubeconfig: str | None = None
    context_name: str | None = None
    kubectl: str = "kubectl"

    @property
    def key(self) -> str:
        return data_source_key(
            "kubernetes", [asdict(self.options), self.kubeconfig, self.context_name, self.kubectl]
        )

    def create_client(self, clients: ClientProvider) -> KubernetesClient:
        return KubernetesClient(self)


class KubernetesClient:
    """Namespace-scoped operations; DataSource + ClientManager own initialization."""

    def __init__(
        self,
        source: KubernetesDataSource,
        *,
        api: _CoreV1API | None = None,
        apps_api: _AppsV1API | None = None,
    ) -> None:
        _validate_options(source.options)
        self._source = source
        self._runtime_kubeconfig = source.kubeconfig
        self._options = source.options
        self._core = api
        self._apps = apps_api
        self._api_client: kubernetes.ApiClient | None = None
        self._disposed = False
        self._stack = AsyncExitStack()
        self._exec_slots = asyncio.Semaphore(source.options.exec_concurrency)
        self._operations: set[asyncio.Task] = set()
        self._disposal: asyncio.Task[None] | None = None

    @property
    def _api(self) -> _CoreV1API:
        if self._core is None or self._disposed:
            raise RuntimeError("Kubernetes client is not initialized")
        return self._core

    async def initialize(self) -> None:
        if self._disposed:
            raise RuntimeError("Kubernetes client is disposed")
        if self._core is not None:
            return
        configuration = kubernetes.Configuration()
        if self._source.kubeconfig:
            await config.load_kube_config(
                config_file=str(Path(self._source.kubeconfig).expanduser()),
                context=self._source.context_name,
                client_configuration=configuration,
            )
        else:
            config.load_incluster_config(client_configuration=configuration)
            # kubectl otherwise may prefer a mounted ~/.kube/config or KUBECONFIG and
            # reach a different cluster from the API client. Pin the same in-cluster identity.
            from kubernetes_asyncio.config.incluster_config import SERVICE_TOKEN_FILENAME

            directory = tempfile.TemporaryDirectory(prefix="kube-access-")
            self._stack.callback(directory.cleanup)
            kubeconfig = Path(directory.name) / "config"
            kubeconfig.write_text(
                json.dumps(
                    {
                        "apiVersion": "v1",
                        "kind": "Config",
                        "clusters": [
                            {
                                "name": "target",
                                "cluster": {
                                    "server": configuration.host,
                                    "certificate-authority": configuration.ssl_ca_cert,
                                },
                            }
                        ],
                        "users": [
                            {"name": "identity", "user": {"tokenFile": SERVICE_TOKEN_FILENAME}}
                        ],
                        "contexts": [
                            {"name": "target", "context": {"cluster": "target", "user": "identity"}}
                        ],
                        "current-context": "target",
                    }
                )
            )
            kubeconfig.chmod(0o600)
            self._runtime_kubeconfig = str(kubeconfig)
        configuration.connection_pool_maxsize = self._options.connection_pool_maxsize
        self._api_client = kubernetes.ApiClient(configuration)
        self._core = kubernetes.CoreV1Api(self._api_client)
        self._apps = kubernetes.AppsV1Api(self._api_client)

    @property
    def access(self) -> KubernetesAccess:
        # The API and kubectl transports use the same explicit cluster/namespace/context.
        return KubernetesAccess(
            self._runtime_kubeconfig,
            self._options.namespace,
            self._source.context_name if self._source.kubeconfig else None,
            self._source.kubectl,
        )

    async def create_pod(self, spec: PodSpec) -> Pod:
        """Create exactly once. Existing names are conflicts, not implicit adoption."""
        if not spec.name or not spec.image:
            raise ValueError("Pod name and image are required")
        body = kubernetes.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=kubernetes.V1ObjectMeta(
                name=spec.name, namespace=self._options.namespace, labels=spec.labels
            ),
            spec=kubernetes.V1PodSpec(
                restart_policy=spec.restart_policy,
                containers=[
                    kubernetes.V1Container(
                        name=spec.container,
                        image=spec.image,
                        command=list(spec.command) or None,
                        args=list(spec.args) or None,
                        env=[kubernetes.V1EnvVar(name=k, value=v) for k, v in spec.env.items()],
                        resources=kubernetes.V1ResourceRequirements(
                            requests=spec.requests, limits=spec.limits
                        ),
                    )
                ],
            ),
        )
        created = await self._api.create_namespaced_pod(
            self._options.namespace, body=body, _request_timeout=self._options.request_timeout_s
        )
        return _pod_from(created)

    async def execute(
        self,
        ref: PodRef,
        command: Sequence[str],
        *,
        stdin: bytes = b"",
        container: str | None = None,
    ) -> ExecResult:
        """Bounded exec, without shell interpolation. Nonzero exit is an explicit result.

        Kubernetes exec has no UID precondition. Check before and after access and
        report replacement; unlike delete this cannot provide atomic UID fencing.
        """
        _ = self._api  # Require initialization before scheduling external work.

        async def operation() -> ExecResult:
            async with asyncio.timeout(self._options.exec_timeout_s), self._exec_slots:
                await self._require_identity(ref)
                args = ["exec", "-i", ref.name]
                if container:
                    args += ["-c", container]
                result = await execute(
                    self.access.command(*args, "--", *command),
                    stdin=stdin,
                    timeout_s=self._options.exec_timeout_s,
                    max_bytes=self._options.max_exec_bytes,
                )
                await self._require_identity(ref)
                return result

        task = asyncio.create_task(operation())
        self._operations.add(task)
        try:
            return await task
        finally:
            self._operations.discard(task)

    async def port_forward(self, ref: PodRef, remote_port: int) -> Endpoint:
        """Open a localhost tunnel owned by this client and closed at root finalize."""
        _ = self._api

        async def operation() -> Endpoint:
            await self._require_identity(ref)
            transport = PortForwardTransport(
                self.access, "pod/" + ref.name, remote_port, self._options.request_timeout_s
            )
            stack = AsyncExitStack()
            try:
                endpoint = await stack.enter_async_context(
                    transport.connect(Endpoint(ref.name, remote_port))
                )
                await self._require_identity(ref)
            except BaseException:
                await stack.aclose()
                raise
            self._stack.push_async_callback(stack.aclose)
            return endpoint

        task = asyncio.create_task(operation())
        self._operations.add(task)
        try:
            return await task
        finally:
            self._operations.discard(task)

    async def _require_identity(self, ref: PodRef) -> None:
        _validate_pod_ref(ref)
        observed = await self.get_pod(ref.name)
        if observed.uid != ref.uid:
            raise RuntimeError("Pod identity changed")

    async def wait_deleted(self, ref: PodRef, *, timeout_s: float, interval_s: float) -> None:
        """A replacement UID means the requested instance has been deleted."""
        _validate_pod_ref(ref)

        async def check() -> bool | None:
            pod = await self._read_pod(ref.name, allow_not_found=True)
            return True if pod is None or str(pod.metadata.uid) != ref.uid else None

        await _poll(
            check,
            timeout_s=timeout_s,
            interval_s=interval_s,
            description=f"Pod {ref.name!r} uid {ref.uid!r} to be deleted",
        )

    async def list_pods(self, selector: str) -> list[Pod]:
        """Return a deterministic Pod snapshot matching a label selector."""
        try:
            result = await self._api.list_namespaced_pod(
                self._options.namespace,
                label_selector=selector,
                _request_timeout=self._options.request_timeout_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"list Pods in namespace {self._options.namespace!r} "
                f"with selector {selector!r}: {exc}"
            ) from exc
        return sorted((_pod_from(item) for item in result.items), key=lambda pod: pod.name)

    async def list_service_pods(self, name: str) -> list[Pod]:
        """List Pods selected by a Kubernetes Service, not by name prefix.

        A missing Service raises ResourceNotFoundError. A selectorless Service
        cannot identify Pods and raises ValueError instead of listing the namespace.
        """
        if not name.strip():
            raise ValueError("Service name is required")
        try:
            resource = await self._api.read_namespaced_service(
                name, self._options.namespace, _request_timeout=self._options.request_timeout_s
            )
        except ApiException as exc:
            if exc.status == 404:
                raise ResourceNotFoundError(
                    f"Service {name!r} in namespace {self._options.namespace!r} not found"
                ) from exc
            raise
        selector = resource.spec.selector if resource.spec is not None else None
        if not selector:
            raise ValueError(f"Service {name!r} has no Pod selector")
        return await self.list_pods(
            label_selector(kubernetes.V1LabelSelector(match_labels=selector))
        )

    async def list_deployment_pods(self, name: str) -> list[Pod]:
        """List Pods matching a Deployment's complete label selector.

        This reports selector membership, not an ownerReference ownership claim.
        The caller decides readiness, termination and sample-selection policy.
        """
        if not name.strip():
            raise ValueError("Deployment name is required")
        _ = self._api
        if self._apps is None:
            raise RuntimeError("Kubernetes Apps API is not initialized")
        try:
            resource = await self._apps.read_namespaced_deployment(
                name, self._options.namespace, _request_timeout=self._options.request_timeout_s
            )
        except ApiException as exc:
            if exc.status == 404:
                raise ResourceNotFoundError(
                    f"Deployment {name!r} in namespace {self._options.namespace!r} not found"
                ) from exc
            raise
        selector = resource.spec.selector if resource.spec is not None else None
        return await self.list_pods(label_selector(selector))

    async def get_pod(self, name: str) -> Pod:
        """Read one Pod's stable observation."""
        if not name.strip():
            raise ValueError(f"get Pod in namespace {self._options.namespace!r}: name is required")
        item = await self._read_pod(name, allow_not_found=False)
        assert item is not None
        return _pod_from(item)

    async def delete_pod(self, ref: PodRef) -> None:
        """Delete one physical Pod using its normal termination grace period."""
        await self._delete_pod(ref, grace_period_seconds=None)

    async def force_delete_pod(self, ref: PodRef) -> None:
        """Delete one physical Pod with a zero-second termination grace period."""
        await self._delete_pod(ref, grace_period_seconds=0)

    async def wait_replacement(
        self,
        selector: str,
        previous: Sequence[Pod],
        *,
        timeout_s: float,
        interval_s: float,
    ) -> Pod:
        """Wait for a matching Pod whose UID was absent from the prior snapshot."""
        if not previous:
            raise ValueError("wait for replacement Pod: previous snapshot is empty")
        known_uids = {pod.uid for pod in previous}
        if "" in known_uids:
            raise ValueError("wait for replacement Pod: previous Pod has no UID")

        async def check() -> Pod | None:
            pods = await self.list_pods(selector)
            return next((pod for pod in pods if pod.uid not in known_uids), None)

        return await _poll(
            check,
            timeout_s=timeout_s,
            interval_s=interval_s,
            description=(
                f"replacement Pod in namespace {self._options.namespace!r} "
                f"with selector {selector!r}"
            ),
        )

    async def wait_ready(
        self,
        ref: PodRef,
        *,
        timeout_s: float,
        interval_s: float,
    ) -> Pod:
        """Wait until the exact Pod is Ready and not terminating."""
        return await self._wait_pod(
            ref,
            timeout_s=timeout_s,
            interval_s=interval_s,
            condition="Ready",
            matches=lambda pod: pod.ready and not pod.deleting,
        )

    async def wait_unschedulable(
        self,
        ref: PodRef,
        *,
        timeout_s: float,
        interval_s: float,
    ) -> Pod:
        """Wait for PodScheduled=False with reason Unschedulable."""
        return await self._wait_pod(
            ref,
            timeout_s=timeout_s,
            interval_s=interval_s,
            condition="Unschedulable",
            matches=lambda pod: pod.unschedulable,
        )

    async def list_events(self, ref: PodRef) -> list[Event]:
        """Return deterministically ordered Events for one physical Pod."""
        _validate_pod_ref(ref)
        field_selector = f"involvedObject.uid={ref.uid}"
        try:
            result = await self._api.list_namespaced_event(
                self._options.namespace,
                field_selector=field_selector,
                _request_timeout=self._options.request_timeout_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"list Events for Pod {ref.name!r} uid {ref.uid!r} "
                f"in namespace {self._options.namespace!r}: {exc}"
            ) from exc

        events = [
            _event_from(item)
            for item in result.items
            if str(item.involved_object.uid or "") == ref.uid
        ]
        return sorted(events, key=lambda event: (event.observed_at, event.reason))

    async def dispose(self) -> None:
        if self._disposal is None:
            self._disposed = True
            self._disposal = asyncio.create_task(self._dispose())
        await asyncio.shield(self._disposal)

    async def _dispose(self) -> None:
        tasks = list(self._operations)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._stack.aclose()
        finally:
            if self._api_client is not None:
                await self._api_client.close()
                self._api_client = None
            self._core = None
            self._apps = None

    async def _delete_pod(
        self,
        ref: PodRef,
        *,
        grace_period_seconds: int | None,
    ) -> None:
        _validate_pod_ref(ref)
        body = kubernetes.V1DeleteOptions(
            preconditions=kubernetes.V1Preconditions(uid=ref.uid),
            propagation_policy="Background",
            grace_period_seconds=grace_period_seconds,
        )
        try:
            await self._api.delete_namespaced_pod(
                ref.name,
                self._options.namespace,
                body=body,
                _request_timeout=self._options.request_timeout_s,
            )
        except Exception as exc:
            raise RuntimeError(
                f"delete Pod {ref.name!r} uid {ref.uid!r} "
                f"in namespace {self._options.namespace!r}: {exc}"
            ) from exc

    async def _wait_pod(
        self,
        ref: PodRef,
        *,
        timeout_s: float,
        interval_s: float,
        condition: str,
        matches: Callable[[Pod], bool],
    ) -> Pod:
        _validate_pod_ref(ref)

        async def check() -> Pod | None:
            item = await self._read_pod(ref.name, allow_not_found=True)
            if item is None:
                return None
            pod = _pod_from(item)
            if pod.uid != ref.uid:
                raise RuntimeError(
                    f"Pod {ref.name!r} identity changed from uid {ref.uid!r} to {pod.uid!r}"
                )
            return pod if matches(pod) else None

        return await _poll(
            check,
            timeout_s=timeout_s,
            interval_s=interval_s,
            description=(
                f"Pod {ref.name!r} uid {ref.uid!r} in namespace "
                f"{self._options.namespace!r} to become {condition}"
            ),
        )

    async def _read_pod(self, name: str, *, allow_not_found: bool) -> kubernetes.V1Pod | None:
        try:
            return await self._api.read_namespaced_pod(
                name,
                self._options.namespace,
                _request_timeout=self._options.request_timeout_s,
            )
        except ApiException as exc:
            if allow_not_found and exc.status == 404:
                return None
            raise RuntimeError(
                f"get Pod {name!r} in namespace {self._options.namespace!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"get Pod {name!r} in namespace {self._options.namespace!r}: {exc}"
            ) from exc


async def _poll(
    check: Callable[[], Awaitable[T | None]],
    *,
    timeout_s: float,
    interval_s: float,
    description: str,
) -> T:
    if timeout_s <= 0:
        raise ValueError("poll timeout must be positive")
    if interval_s <= 0:
        raise ValueError("poll interval must be positive")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description}")
        try:
            result = await asyncio.wait_for(check(), timeout=remaining)
        except TimeoutError as exc:
            raise TimeoutError(f"timed out waiting for {description}") from exc
        if result is not None:
            return result
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {description}")
        await asyncio.sleep(min(interval_s, remaining))


def _validate_options(options: Options) -> None:
    if not options.namespace.strip():
        raise ValueError("open Kubernetes client: namespace is required")
    if options.request_timeout_s <= 0:
        raise ValueError("open Kubernetes client: request timeout must be positive")
    if min(options.exec_timeout_s, options.exec_concurrency, options.max_exec_bytes) <= 0:
        raise ValueError("exec limits must be positive")
    if options.connection_pool_maxsize <= 0:
        raise ValueError("open Kubernetes client: connection pool max size must be positive")


def _validate_pod_ref(ref: PodRef) -> None:
    if not ref.name.strip():
        raise ValueError("Pod name is required")
    if not ref.uid.strip():
        raise ValueError("Pod UID is required")


def _pod_from(item: kubernetes.V1Pod) -> Pod:
    ready = False
    unschedulable = False
    reason = ""
    message = ""
    status = item.status or kubernetes.V1PodStatus()
    for condition in status.conditions or []:
        if condition.type == "Ready":
            ready = condition.status == "True" and item.metadata.deletion_timestamp is None
        elif (
            condition.type == "PodScheduled"
            and condition.status == "False"
            and condition.reason == "Unschedulable"
        ):
            unschedulable = True
            reason = condition.reason or ""
            message = condition.message or ""
    return Pod(
        name=item.metadata.name or "",
        uid=str(item.metadata.uid or ""),
        labels=dict(item.metadata.labels or {}),
        phase=status.phase or "",
        ready=ready,
        deleting=item.metadata.deletion_timestamp is not None,
        unschedulable=unschedulable,
        reason=reason,
        message=message,
        containers=tuple(
            Container(c.name, c.restart_count or 0)
            for c in (status.container_statuses or []) + (status.init_container_statuses or [])
        ),
    )


def _event_from(item: kubernetes.CoreV1Event) -> Event:
    return Event(
        type=item.type or "",
        reason=item.reason or "",
        message=item.message or "",
        count=item.count or 0,
        observed_at=_event_observed_at(item),
    )


def _event_observed_at(item: kubernetes.CoreV1Event) -> datetime:
    series_time = item.series.last_observed_time if item.series is not None else None
    observed = next(
        (
            value
            for value in (
                item.event_time,
                series_time,
                item.last_timestamp,
                item.first_timestamp,
                item.metadata.creation_timestamp,
            )
            if value is not None
        ),
        datetime.min.replace(tzinfo=UTC),
    )
    if observed.tzinfo is None:
        return observed.replace(tzinfo=UTC)
    return observed
