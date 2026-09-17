import subprocess

import pytest
from harness_common.host import Host

from harness_toolbox.environment import KubernetesEnvironment, _native_access
from harness_toolbox.host import command, observe_local
from harness_toolbox.kube import Options


def test_remote_arguments_remain_literal():
    value = "file with ' quote; $(exit 7)\nnext"
    argv = command(Host("devbox", "ssh", "builder"), ["printf", "%s", value])
    result = subprocess.run(["sh", "-c", argv[-1]], capture_output=True, check=True)
    assert result.stdout.decode() == value


def test_local_host_facts():
    assert command(None, ["printf", "%s", "local"]) == ["printf", "%s", "local"]
    facts = observe_local()
    assert facts.values["os"]
    assert facts.values["kernel_version"]
    assert facts.source and facts.observed_at


def test_remote_kubeconfig_is_not_read_locally():
    environment = KubernetesEnvironment(
        "test", "/remote/config", host=Host("builder", "ssh", "builder")
    )
    with pytest.raises(ValueError, match="local cluster access"):
        _native_access(
            environment, Options(namespace="test", request_timeout_s=5, connection_pool_maxsize=2)
        )
