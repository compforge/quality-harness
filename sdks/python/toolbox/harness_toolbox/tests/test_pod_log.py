import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from harness_toolbox import ClientManager
from harness_toolbox.kube import KubernetesClient, KubernetesDataSource, Options
from harness_toolbox.kube.model import Container, Pod
from harness_toolbox.pod_log import PodLogDataSource, PodLogTarget


class TestKubernetesSource(KubernetesDataSource):
    __test__ = False

    def create_client(self, clients):
        return TestKubernetesClient(self)


class TestKubernetesClient(KubernetesClient):
    __test__ = False

    async def initialize(self):
        pass

    async def get_pod(self, name):
        return Pod(name, "uid-1", {}, "Running", True, False, False, "", "", (Container("app", 0),))


def kube_source(kubectl):
    return TestKubernetesSource(Options("quality", 10, 4), kubectl=str(kubectl))


@pytest.fixture
def kubectl(tmp_path):
    script = tmp_path / "kubectl"
    script.write_text("""#!/usr/bin/env python3
import sys, json, time
if 'get' in sys.argv:
    print(json.dumps({'metadata': {'uid': 'uid-1'}, 'status': {'containerStatuses': [{'name': 'app', 'restartCount': 0}]}}))
else:
    print('2026-09-10T00:00:01Z trace-a trace-b')
    print('2026-09-10T00:00:02Z trace-b')
    print('2026-09-10T00:00:11Z trace-a')
""")
    script.chmod(0o755)
    return script


def target():
    since = datetime(2026, 9, 10, tzinfo=UTC)
    return PodLogTarget("pod-a", "uid-1", "app", 0, since, since + timedelta(seconds=10))


async def test_capture_reused_and_ids_filter_local_absolute_window(kubectl):
    source = PodLogDataSource(kube_source(kubectl))
    async with ClientManager() as clients:
        logs = await clients.get(source)
        first, second = await asyncio.gather(logs.capture(target()), logs.capture(target()))
        assert first is second
        assert not first.truncated
        lines = first.matching_lines(("trace-a", "trace-b"))
        assert len(lines["trace-a"]) == 1
        assert len(lines["trace-b"]) == 2
        path = first.path
    assert not path.exists()


async def test_global_byte_budget_and_identity_fencing(kubectl):
    source = PodLogDataSource(kube_source(kubectl), max_total_bytes=50)
    async with ClientManager() as clients:
        logs = await clients.get(source)
        first, second = await asyncio.gather(
            logs.capture(target()), logs.capture(replace(target(), pod="pod-b"))
        )
        assert first.size_bytes + second.size_bytes == 50
        assert first.truncated and second.truncated
        with pytest.raises(RuntimeError, match="identity changed"):
            await logs.capture(replace(target(), uid="replacement"))


async def test_cancelled_waiter_does_not_cancel_shared_capture(kubectl):
    source = PodLogDataSource(kube_source(kubectl))
    async with ClientManager() as clients:
        logs = await clients.get(source)
        first = asyncio.create_task(logs.capture(target()))
        second = asyncio.create_task(logs.capture(target()))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).size_bytes > 0
