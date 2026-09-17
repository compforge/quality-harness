import subprocess
import sys
from dataclasses import asdict, replace

from harness_common import ClientProvider, Environment

from harness_toolbox.environment import KubernetesEnvironment, parse_environment


def test_environment_declaration_does_not_import_optional_drivers():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """import sys
import harness_common
from harness_toolbox.environment import parse_environment
env = parse_environment({"name": "dev", "kind": "kubernetes", "kubeconfig": "/not-opened"})
assert env.client_key
assert "harness_toolbox.kube" not in sys.modules
assert "kubernetes_asyncio" not in sys.modules
assert not hasattr(harness_common, "ClientFactory")
assert not hasattr(harness_common, "KubernetesEnvironment")
assert not hasattr(harness_common, "_ClientBorrower")
""",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_environment_roundtrip_preserves_access_identity_and_limits():
    env = KubernetesEnvironment("dev", "/config", "ctx")
    configured = replace(env, options=replace(env.options, namespace="app", request_timeout_s=7))
    restored = parse_environment({**asdict(configured), "kind": configured.kind})
    assert isinstance(restored, KubernetesEnvironment)
    assert isinstance(restored, Environment)
    provider: ClientProvider = restored
    assert provider.client_key == configured.client_key
    assert restored.options == configured.options
    assert env == configured
    assert env.client_key != configured.client_key


def test_optional_image_registry_is_metadata_not_client_identity():
    env = KubernetesEnvironment("dev", "/config", "ctx")
    assert env.image_registry is None
    configured = replace(env, image_registry="registry.example.com/team")
    restored = parse_environment({**asdict(configured), "kind": configured.kind})
    assert restored.image_registry == "registry.example.com/team"
    assert restored.client_key == env.client_key
