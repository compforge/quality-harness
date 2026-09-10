from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kubernetes_asyncio import client as kubernetes
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.kube import KubernetesClient, KubernetesDataSource, Options, PodRef

TEST_NAMESPACE = "quality"
TEST_OPTIONS = Options(
    namespace=TEST_NAMESPACE,
    request_timeout_s=3,
    connection_pool_maxsize=4,
)


class FakeCoreV1API:
    def __init__(
        self,
        pods: list[kubernetes.V1Pod] | None = None,
        events: list[kubernetes.CoreV1Event] | None = None,
    ) -> None:
        self.pods = pods or []
        self.events = events or []
        self.delete_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.list_pod_calls: list[tuple[str, dict[str, Any]]] = []

    async def create_namespaced_pod(self, namespace: str, **kwargs: Any) -> kubernetes.V1Pod:
        body = kwargs["body"]
        assert body.metadata.namespace == namespace
        body.metadata.uid = "created-uid"
        self.pods.append(body)
        return body

    async def list_namespaced_pod(self, namespace: str, **kwargs: Any) -> kubernetes.V1PodList:
        self.list_pod_calls.append((namespace, kwargs))
        return kubernetes.V1PodList(items=self.pods)

    async def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Pod:
        del namespace, kwargs
        for pod in self.pods:
            if pod.metadata.name == name:
                return pod
        raise ApiException(status=404, reason="Not Found")

    async def delete_namespaced_pod(self, name: str, namespace: str, **kwargs: Any) -> None:
        self.delete_calls.append((name, namespace, kwargs))

    async def list_namespaced_event(
        self, namespace: str, **kwargs: Any
    ) -> kubernetes.CoreV1EventList:
        del namespace, kwargs
        return kubernetes.CoreV1EventList(items=self.events)


class SlowReadCoreV1API(FakeCoreV1API):
    async def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: Any
    ) -> kubernetes.V1Pod:
        del name, namespace, kwargs
        await asyncio.sleep(1)
        raise AssertionError("wait timeout did not cancel the Kubernetes request")


async def test_list_pods_projects_stable_state_and_sorts() -> None:
    api = FakeCoreV1API(
        [
            pod("worker-b", "uid-b", ready=True),
            pod("worker-a", "uid-a", unschedulable=True),
        ]
    )
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=api)

    pods = await client.list_pods("app=worker")

    assert [item.name for item in pods] == ["worker-a", "worker-b"]
    assert pods[0].unschedulable
    assert pods[0].message == "insufficient cpu"
    assert pods[1].ready
    assert api.list_pod_calls == [
        (
            TEST_NAMESPACE,
            {"label_selector": "app=worker", "_request_timeout": 3},
        )
    ]


@pytest.mark.parametrize(
    ("force", "expected_grace_period"),
    [(False, None), (True, 0)],
)
async def test_delete_pod_uses_uid_precondition(
    force: bool, expected_grace_period: int | None
) -> None:
    api = FakeCoreV1API([pod("worker", "uid-worker")])
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=api)
    ref = PodRef(name="worker", uid="uid-worker")

    if force:
        await client.force_delete_pod(ref)
    else:
        await client.delete_pod(ref)

    name, namespace, kwargs = api.delete_calls[0]
    body = kwargs["body"]
    assert (name, namespace) == ("worker", TEST_NAMESPACE)
    assert body.preconditions.uid == "uid-worker"
    assert body.propagation_policy == "Background"
    assert body.grace_period_seconds == expected_grace_period
    assert kwargs["_request_timeout"] == 3


async def test_wait_replacement_then_ready() -> None:
    api = FakeCoreV1API(
        [
            pod("worker-old", "uid-old", ready=True),
            pod("worker-new", "uid-new", ready=True),
        ]
    )
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=api)
    previous = await client.get_pod("worker-old")

    replacement = await client.wait_replacement(
        "app=worker", [previous], timeout_s=1, interval_s=0.001
    )
    ready = await client.wait_ready(replacement.ref(), timeout_s=1, interval_s=0.001)

    assert replacement.uid == "uid-new"
    assert ready.ready


async def test_wait_ready_rejects_reused_name() -> None:
    client = KubernetesClient(
        KubernetesDataSource(TEST_OPTIONS),
        api=FakeCoreV1API([pod("worker", "uid-new", ready=True)]),
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        await client.wait_ready(
            PodRef(name="worker", uid="uid-old"),
            timeout_s=1,
            interval_s=0.001,
        )


async def test_wait_ready_bounds_inflight_kubernetes_request() -> None:
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=SlowReadCoreV1API())

    with pytest.raises(TimeoutError, match="timed out waiting"):
        await client.wait_ready(
            PodRef(name="worker", uid="uid-worker"),
            timeout_s=0.01,
            interval_s=0.001,
        )


async def test_wait_unschedulable() -> None:
    client = KubernetesClient(
        KubernetesDataSource(TEST_OPTIONS),
        api=FakeCoreV1API([pod("worker", "uid-worker", unschedulable=True)]),
    )

    observed = await client.wait_unschedulable(
        PodRef(name="worker", uid="uid-worker"),
        timeout_s=1,
        interval_s=0.001,
    )

    assert observed.unschedulable
    assert observed.message == "insufficient cpu"


async def test_list_events_scopes_by_uid_and_sorts() -> None:
    first = datetime(2026, 8, 21, 10, tzinfo=UTC)
    api = FakeCoreV1API(
        events=[
            event("late", "uid-worker", "Pulled", first + timedelta(seconds=1)),
            event("early", "uid-worker", "Scheduled", first),
            event("other", "uid-other", "Ignored", first),
        ]
    )
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=api)

    events = await client.list_events(PodRef(name="worker", uid="uid-worker"))

    assert [item.reason for item in events] == ["Scheduled", "Pulled"]


@pytest.mark.parametrize(
    "options",
    [
        Options(namespace="", request_timeout_s=1, connection_pool_maxsize=1),
        Options(namespace="quality", request_timeout_s=0, connection_pool_maxsize=1),
        Options(namespace="quality", request_timeout_s=1, connection_pool_maxsize=0),
    ],
)
def test_client_rejects_missing_scope_or_limits(options: Options) -> None:
    with pytest.raises(ValueError):
        KubernetesClient(KubernetesDataSource(options), api=FakeCoreV1API())


def pod(
    name: str,
    uid: str,
    *,
    ready: bool = False,
    unschedulable: bool = False,
) -> kubernetes.V1Pod:
    conditions: list[kubernetes.V1PodCondition] = []
    if ready:
        conditions.append(kubernetes.V1PodCondition(type="Ready", status="True"))
    if unschedulable:
        conditions.append(
            kubernetes.V1PodCondition(
                type="PodScheduled",
                status="False",
                reason="Unschedulable",
                message="insufficient cpu",
            )
        )
    return kubernetes.V1Pod(
        metadata=kubernetes.V1ObjectMeta(
            name=name,
            uid=uid,
            namespace=TEST_NAMESPACE,
            labels={"app": "worker"},
        ),
        status=kubernetes.V1PodStatus(phase="Running", conditions=conditions),
    )


def event(
    name: str,
    uid: str,
    reason: str,
    observed_at: datetime,
) -> kubernetes.CoreV1Event:
    return kubernetes.CoreV1Event(
        metadata=kubernetes.V1ObjectMeta(name=name, namespace=TEST_NAMESPACE),
        involved_object=kubernetes.V1ObjectReference(
            name="worker", uid=uid, namespace=TEST_NAMESPACE
        ),
        type="Normal",
        reason=reason,
        message=reason.lower(),
        count=1,
        last_timestamp=observed_at,
    )


async def test_create_inspect_delete_and_wait_for_exact_instance():
    from harness_toolbox.kube import PodSpec

    api = FakeCoreV1API()
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS), api=api)
    created = await client.create_pod(
        PodSpec("worker", "example/worker:1", requests={"cpu": "100m"})
    )
    assert created.uid == "created-uid"
    assert api.pods[0].spec.containers[0].resources.requests == {"cpu": "100m"}
    await client.delete_pod(created.ref())
    api.pods = [pod("worker", "replacement")]
    await client.wait_deleted(created.ref(), timeout_s=1, interval_s=0.01)
    assert api.delete_calls[0][2]["body"].preconditions.uid == "created-uid"
    await client.dispose()
    with pytest.raises(RuntimeError, match="not initialized"):
        await client.get_pod("worker")


async def test_exec_stdin_exit_status_and_uid_check(tmp_path):
    script = tmp_path / "kubectl"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\nsys.stderr.write('diagnostic')\nsys.exit(7)\n"
    )
    script.chmod(0o755)
    api = FakeCoreV1API([pod("worker", "uid")])
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS, kubectl=str(script)), api=api)
    result = await client.execute(PodRef("worker", "uid"), ["cat"], stdin=b"private payload")
    assert (result.stdout, result.stderr, result.exit_code) == (
        b"private payload",
        b"diagnostic",
        7,
    )
    with pytest.raises(RuntimeError, match="identity changed"):
        await client.execute(PodRef("worker", "old-uid"), ["cat"])
    await client.dispose()


async def test_root_disposal_drains_exec(tmp_path):
    script = tmp_path / "kubectl"
    script.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
    script.chmod(0o755)
    client = KubernetesClient(
        KubernetesDataSource(TEST_OPTIONS, kubectl=str(script)),
        api=FakeCoreV1API([pod("worker", "uid")]),
    )
    task = asyncio.create_task(client.execute(PodRef("worker", "uid"), ["sleep", "60"]))
    await asyncio.sleep(0.1)
    await asyncio.wait_for(client.dispose(), 3)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_port_forward_is_owned_by_client(tmp_path):
    script = tmp_path / "kubectl"
    script.write_text(
        "#!/usr/bin/env python3\nimport time\nprint('Forwarding from 127.0.0.1:12345 -> 3306', flush=True)\ntime.sleep(60)\n"
    )
    script.chmod(0o755)
    client = KubernetesClient(
        KubernetesDataSource(TEST_OPTIONS, kubectl=str(script)),
        api=FakeCoreV1API([pod("worker", "uid")]),
    )
    endpoint = await client.port_forward(PodRef("worker", "uid"), 3306)
    assert endpoint.host == "127.0.0.1" and endpoint.port == 12345
    await asyncio.wait_for(client.dispose(), 3)


async def test_in_cluster_api_and_exec_pin_the_same_identity(monkeypatch):
    import json
    from pathlib import Path

    from certifi import where

    def load(*, client_configuration):
        client_configuration.host = "https://cluster.example:443"
        client_configuration.ssl_ca_cert = where()

    monkeypatch.setattr("harness_toolbox.kube.client.config.load_incluster_config", load)
    client = KubernetesClient(KubernetesDataSource(TEST_OPTIONS))
    await client.initialize()
    path = Path(client.access.kubeconfig)
    config = json.loads(path.read_text())
    assert config["clusters"][0]["cluster"]["server"] == "https://cluster.example:443"
    assert "tokenFile" in config["users"][0]["user"]
    assert str(path) in client.access.command("get", "pods")
    await client.dispose()
    assert not path.exists()
