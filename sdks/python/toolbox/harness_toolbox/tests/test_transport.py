from unittest.mock import AsyncMock

import pytest

from harness_toolbox import transport
from harness_toolbox.transport import KubernetesAccess, PodPythonTransport


@pytest.mark.parametrize("container", [None, "business"])
async def test_pod_python_preserves_container_and_stdin(monkeypatch, container):
    run = AsyncMock(return_value=b"result")
    monkeypatch.setattr(transport, "run", run)
    access = KubernetesAccess("/config", "ns", "context")
    source = PodPythonTransport(access, "worker", container=container)
    assert (
        await source.run("script", {"password": "secret"}, timeout_s=7, max_bytes=100) == b"result"
    )
    args = ["exec", "-i", "worker"]
    if container:
        args += ["-c", container]
    run.assert_awaited_once_with(
        access.command(*args, "--", "python3", "-c", "script"),
        stdin=b'{"password": "secret"}',
        timeout_s=7,
        max_bytes=100,
    )
    assert "secret" not in str(run.call_args.args[0])


def test_container_participates_in_transport_identity():
    access = KubernetesAccess(None, "ns")
    assert (
        PodPythonTransport(access, "pod", container="a").key
        != PodPythonTransport(access, "pod", container="b").key
    )
