import shlex
from pathlib import Path

import pytest
from harness_common import Host, HostEnvironment, KubernetesEnvironment

from perf_harness.config import _parse_deployer, _parse_service
from perf_harness.model import Deployment, ResourceProfile
from perf_harness.observe import k8s
from perf_harness.observe.base import ProbeContext


def service_config():
    return {
        "name": "chat",
        "base_url": "http://chat:8000",
        "namespace": "test",
        "k8s_selector": "app=chat",
        "environment": {
            "name": "dev",
            "kind": "kubernetes",
            "host": {"name": "devbox", "transport": "ssh", "address": "devbox"},
            "kubeconfig": "/remote/config with spaces",
            "context": "dev-context",
        },
    }


def test_environment_inheritance_and_explicit_local_override():
    root = _parse_service(service_config())
    assert isinstance(root.environment, KubernetesEnvironment)
    assert root.environment.host == Host("devbox", "ssh", "devbox")
    assert root.base_url == "http://chat:8000"
    downstream = _parse_service({"name": "worker"}, root.environment)
    assert downstream.environment == root.environment
    local = _parse_service(
        {"environment": {"host": None, "kubeconfig": "~/.kube/config"}}, root.environment
    )
    assert local.environment.host is None
    assert local.environment.kubeconfig == str(Path.home() / ".kube/config")
    direct = _parse_service({"environment": {"kind": "host"}}, root.environment)
    assert isinstance(direct.environment, HostEnvironment)
    assert direct.environment.host == root.environment.host


@pytest.mark.parametrize("host", [None, {"name": "local", "transport": "local"}])
def test_legacy_local_environment(host):
    service = _parse_service(
        {"environment": {"name": "dev", "kubeconfig": "~/.kube/config", "host": host}}
    )
    assert isinstance(service.environment, KubernetesEnvironment)
    assert service.environment.kubeconfig == str(Path.home() / ".kube/config")


@pytest.mark.parametrize(
    "environment",
    [
        "dev",
        {"hosst": {}},
        {"host": {"name": "devbox", "transport": "ssh"}},
        {"host": {"name": "local", "address": "devbox"}},
        {"kind": "host", "kubeconfig": "/config"},
    ],
)
def test_invalid_environment_is_rejected(environment):
    with pytest.raises(ValueError):
        _parse_service({"environment": environment})


@pytest.mark.parametrize(
    "probe_type, responses",
    [
        (k8s.KubectlTopProbe, ["chat-1 50m 100Mi"]),
        (k8s.PerWorkerRSSProbe, ["chat-1", "PID RSS COMMAND\n1 1024 gunicorn"]),
        (k8s.RestartProbe, ["0 1"]),
        (k8s.PodCountProbe, ['{"items": []}']),
        (k8s.ResourceLimitsProbe, ['{"items": []}']),
    ],
)
@pytest.mark.asyncio
async def test_all_kubernetes_probes_use_access_host(monkeypatch, probe_type, responses):
    service = _parse_service(service_config())
    commands = []
    pending = iter(responses)

    async def capture(argv):
        commands.append(argv)
        return next(pending)

    monkeypatch.setattr(k8s, "run_capture", capture)
    await probe_type().sample(ProbeContext(service=service, client=None, t0=0))
    assert len(commands) == len(responses)
    for argv in commands:
        assert argv[:6] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "devbox"]
        remote = shlex.split(argv[6])
        assert remote[:5] == [
            "kubectl",
            "--context",
            "dev-context",
            "--kubeconfig",
            "/remote/config with spaces",
        ]
        assert remote[remote.index("-n") + 1] == "test"


@pytest.mark.asyncio
async def test_host_environment_has_no_kubernetes_samples():
    service = _parse_service({"environment": {"kind": "host", "name": "vm"}})
    assert (
        await k8s.KubectlTopProbe().sample(ProbeContext(service=service, client=None, t0=0)) == {}
    )


@pytest.mark.asyncio
async def test_helm_deploy_rollout_and_teardown_share_host(monkeypatch):
    service = _parse_service(service_config())
    deployer = _parse_deployer(
        {"release": "chat", "chart_path": "/remote/chart", "base_values": "/remote/values.yaml"},
        service,
    )
    commands = []

    async def capture(argv):
        commands.append(argv)
        return ""

    monkeypatch.setattr("perf_harness.deploy.run_capture", capture)
    await deployer.deploy(Deployment(service=service, resources=ResourceProfile(workers=2)))
    await deployer.teardown()
    assert len(commands) == 3
    for argv, program in zip(commands, ["helm", "kubectl", "helm"], strict=True):
        assert argv[0] == "ssh" and argv[5] == "devbox"
        remote = shlex.split(argv[6])
        context_flag = "--kube-context" if program == "helm" else "--context"
        assert remote[:5] == [
            program,
            context_flag,
            "dev-context",
            "--kubeconfig",
            "/remote/config with spaces",
        ]
        if program == "helm":
            assert "/remote/chart" in remote
            assert remote[remote.index("-f") + 1] == "/remote/values.yaml"
    assert "uvicorn.workers=2" in shlex.split(commands[0][6])


def test_remote_paths_are_not_expanded_on_runner():
    config = service_config()
    config["environment"]["kubeconfig"] = "~/remote-config"
    service = _parse_service(config)
    deployer = _parse_deployer(
        {"release": "chat", "chart_path": "~/chart", "base_values": "~/values"}, service
    )
    assert service.environment.kubeconfig == "~/remote-config"
    assert deployer.chart_path == "~/chart"
    assert deployer.base_values == "~/values"


def test_downstream_legacy_kubernetes_fields_infer_kind():
    root = _parse_service({"name": "chat"})
    downstream = _parse_service({"environment": {"kubeconfig": "/worker/config"}}, root.environment)
    assert isinstance(downstream.environment, KubernetesEnvironment)
    assert downstream.environment.kubeconfig == "/worker/config"
