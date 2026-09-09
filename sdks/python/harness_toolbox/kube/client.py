"""Async namespace-scoped Kubernetes driver."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TypeVar

from kubernetes_asyncio import client as kubernetes
from kubernetes_asyncio import config
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.kube.model import Event, Options, Pod, PodRef

T = TypeVar("T")


class _CoreV1API(Protocol):
    async def list_namespaced_pod(
        self, namespace: str, **kwargs: Any
    ) -> kubernetes.V1PodList: ...

    async def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Pod: ...

    async def delete_namespaced_pod(
        self, name: str, namespace: str, **kwargs: Any
    ) -> Any: ...

    async def list_namespaced_event(
        self, namespace: str, **kwargs: Any
    ) -> kubernetes.CoreV1EventList: ...


class Client:
    """Kubernetes operations shared by e2e and performance harnesses.

    Every operation is namespace-scoped. Mutations require both Pod name and
    UID so a delayed action cannot affect a replacement Pod that reused a name.
    """

    def __init__(
        self,
        api: _CoreV1API,
        options: Options,
        *,
        api_client: kubernetes.ApiClient | None = None,
    ) -> None:
        _validate_options(options)
        self._api = api
        self._options = options
        self._api_client = api_client

    @classmethod
    async def from_kubeconfig(
        cls,
        path: str | Path,
        options: Options,
        *,
        context_name: str | None = None,
    ) -> Client:
        """Create a client from an explicit kubeconfig and optional context."""
        if not str(path).strip():
            raise ValueError("open Kubernetes client: kubeconfig path is required")
        configuration = kubernetes.Configuration()
        try:
            await config.load_kube_config(
                config_file=str(path),
                context=context_name,
                client_configuration=configuration,
            )
        except Exception as exc:
            raise RuntimeError(f"load kubeconfig {str(path)!r}: {exc}") from exc
        return cls._from_configuration(configuration, options)

    @classmethod
    async def in_cluster(cls, options: Options) -> Client:
        """Create a client from the service account mounted in a Pod."""
        configuration = kubernetes.Configuration()
        try:
            config.load_incluster_config(client_configuration=configuration)
        except Exception as exc:
            raise RuntimeError(f"load in-cluster Kubernetes config: {exc}") from exc
        return cls._from_configuration(configuration, options)

    @classmethod
    def _from_configuration(
        cls,
        configuration: kubernetes.Configuration,
        options: Options,
    ) -> Client:
        _validate_options(options)
        configuration.connection_pool_maxsize = options.connection_pool_maxsize
        api_client = kubernetes.ApiClient(configuration)
        return cls(kubernetes.CoreV1Api(api_client), options, api_client=api_client)

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
        return sorted(
            (_pod_from(item) for item in result.items), key=lambda pod: pod.name
        )

    async def get_pod(self, name: str) -> Pod:
        """Read one Pod's stable observation."""
        if not name.strip():
            raise ValueError(
                f"get Pod in namespace {self._options.namespace!r}: name is required"
            )
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

    async def aclose(self) -> None:
        """Close the transport when this Client constructed it."""
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

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
                    f"Pod {ref.name!r} identity changed from uid "
                    f"{ref.uid!r} to {pod.uid!r}"
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

    async def _read_pod(
        self, name: str, *, allow_not_found: bool
    ) -> kubernetes.V1Pod | None:
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
    if options.connection_pool_maxsize <= 0:
        raise ValueError(
            "open Kubernetes client: connection pool max size must be positive"
        )


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
    for condition in item.status.conditions or []:
        if condition.type == "Ready":
            ready = (
                condition.status == "True" and item.metadata.deletion_timestamp is None
            )
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
        phase=item.status.phase or "",
        ready=ready,
        deleting=item.metadata.deletion_timestamp is not None,
        unschedulable=unschedulable,
        reason=reason,
        message=message,
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
        datetime.min.replace(tzinfo=timezone.utc),
    )
    if observed.tzinfo is None:
        return observed.replace(tzinfo=timezone.utc)
    return observed
