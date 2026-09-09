"""Unit tests for e2e_harness core components."""

from textwrap import dedent

import pytest

from harness_common import Experiment as BaseExperiment

from e2e_harness.core.config import E2EConfig, Experiment, _interpolate, load_config
from e2e_harness.core.profile import require_profile, require_capability
from e2e_harness.runner.base import Outcome


def test_e2e_experiment_extends_common_identity() -> None:
    config = E2EConfig()
    experiment = Experiment(
        name="api-contract", service=config.service, caseset="widgets"
    )

    assert isinstance(experiment, BaseExperiment)
    assert experiment.caseset == "widgets"


class TestInterpolation:
    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert _interpolate("${FOO}") == "bar"

    def test_var_with_default(self, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        assert _interpolate("${MISSING:-fallback}") == "fallback"

    def test_var_with_default_uses_env_if_present(self, monkeypatch):
        monkeypatch.setenv("PRESENT", "actual")
        assert _interpolate("${PRESENT:-fallback}") == "actual"

    def test_missing_required_var_raises(self, monkeypatch):
        monkeypatch.delenv("REQUIRED", raising=False)
        with pytest.raises(ValueError, match="REQUIRED"):
            _interpolate("${REQUIRED}")

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "hello")
        monkeypatch.setenv("B", "world")
        assert _interpolate("${A} ${B}") == "hello world"


class TestLoadConfig:
    def test_load_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_URL", "http://localhost:8080")
        monkeypatch.setenv("MY_TOKEN", "token-1")

        config = tmp_path / "config.yaml"
        config.write_text(
            dedent("""\
            service:
              name: test-svc
              component:
                repository: {forge: github, path: example/service}
                name: api
              environment:
                name: dev
                kubeconfig: /tmp/kubeconfig
                context: dev-cluster
              base_url: ${MY_URL}
              headers:
                Authorization: Bearer ${MY_TOKEN}
            profile: minimal
            custom:
              ttl: "60"
        """)
        )

        config = load_config(config)
        assert config.service.name == "test-svc"
        assert config.service.component.repository.forge.name == "github"
        assert config.service.component.repository.path == "example/service"
        assert config.service.environment.name == "dev"
        assert config.service.environment.kubeconfig == "/tmp/kubeconfig"
        assert config.service.environment.context == "dev-cluster"
        assert config.service.base_url == "http://localhost:8080"
        assert config.service.headers == {"Authorization": "Bearer token-1"}
        assert config.profile == "minimal"
        assert config.custom["ttl"] == "60"

    def test_load_from_env_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("E2E_BASE_URL", "http://fallback:9090")
        monkeypatch.setenv("E2E_SERVICE_HEADERS", '{"Authorization":"Bearer fallback"}')
        monkeypatch.setenv("E2E_PROFILE", "full")

        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.service.base_url == "http://fallback:9090"
        assert config.service.headers == {"Authorization": "Bearer fallback"}
        assert config.profile == "full"

    def test_load_service_headers_mapping(self, tmp_path, monkeypatch):
        config = tmp_path / "config.yaml"
        config.write_text(
            dedent("""\
            service:
              base_url: http://localhost:8080
              headers:
                X-Top-Tenant-Id: t1
                X-Top-User-Id: u1
        """)
        )

        config = load_config(config)
        assert config.service.headers == {
            "X-Top-Tenant-Id": "t1",
            "X-Top-User-Id": "u1",
        }

    @pytest.mark.parametrize(
        ("content", "field"),
        [
            ("auth:\n  headers: {}\n", "auth"),
            ("service:\n  base_url: http://localhost\n  typo: true\n", "typo"),
        ],
    )
    def test_rejects_unknown_fields(self, tmp_path, content, field):
        config = tmp_path / "config.yaml"
        config.write_text(content)

        with pytest.raises(ValueError, match=rf"unknown field.*{field}"):
            load_config(config)

    def test_rejects_non_mapping_service_headers(self, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("service:\n  headers: bearer-token\n")

        with pytest.raises(ValueError, match="service.headers must be a mapping"):
            load_config(config)

    def test_rejects_invalid_service_headers_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("E2E_SERVICE_HEADERS", "not-json")

        with pytest.raises(ValueError):
            load_config(tmp_path / "nonexistent.yaml")


class TestBuildHeaders:
    def _config(self, **header_kwargs):
        from e2e_harness.core.config import Service

        return E2EConfig(
            service=Service(name="test", base_url="http://localhost", **header_kwargs),
        )

    def test_headers_are_injected_directly(self):
        from e2e_harness.runner.headers import build_headers

        config = self._config(
            headers={"X-Top-Tenant-Id": "t1", "X-Top-User-Id": "u1"},
        )
        headers = build_headers(config)
        assert headers["X-Top-Tenant-Id"] == "t1"
        assert headers["X-Top-User-Id"] == "u1"

    def test_no_service_headers_when_empty(self):
        from e2e_harness.runner.headers import build_headers

        config = self._config()
        headers = build_headers(config)
        assert "X-Top-Tenant-Id" not in headers
        assert headers == {"Content-Type": "application/json"}

    def test_extra_overrides_service_headers(self):
        from e2e_harness.runner.headers import build_headers

        config = self._config(headers={"X-T": "t1"})
        headers = build_headers(config, extra={"X-T": "override"})
        assert headers["X-T"] == "override"

    def test_exclude_drops_after_merge(self):
        from e2e_harness.runner.headers import build_headers

        config = self._config(
            headers={"X-T": "t1", "X-U": "u1"},
        )
        headers = build_headers(config, exclude={"X-T"})
        assert "X-T" not in headers
        assert headers["X-U"] == "u1"

    def test_custom_content_type(self):
        from e2e_harness.runner.headers import build_headers

        config = self._config()
        headers = build_headers(config, content_type="text/event-stream")
        assert headers["Content-Type"] == "text/event-stream"


class TestOutcome:
    def test_field_access(self):
        o = Outcome(status_code=200, body={"data": {"id": "abc", "items": [1, 2, 3]}})
        assert o.field("data.id") == "abc"
        assert o.field("data.items.0") == 1
        assert o.field("data.items.2") == 3
        assert o.field("data.missing") is None
        assert o.field("nonexistent.path") is None

    def test_field_str(self):
        o = Outcome(status_code=200, body={"name": "hello", "count": 42})
        assert o.field_str("name") == "hello"
        assert o.field_str("count") == "42"
        assert o.field_str("missing") == ""

    def test_serialization_roundtrip(self):
        o = Outcome(
            status_code=201, body={"id": "x"}, duration_ms=150, raw=b"raw bytes"
        )
        d = o.to_dict()
        restored = Outcome.from_dict(d)
        assert restored.status_code == 201
        assert restored.body == {"id": "x"}
        assert restored.duration_ms == 150


class TestProfile:
    def test_require_profile_skip(self):
        config = E2EConfig(profile="minimal")
        with pytest.raises(pytest.skip.Exception):
            require_profile(config, "full", "warm_off")

    def test_require_profile_pass(self):
        config = E2EConfig(profile="full")
        require_profile(config, "full", "minimal")  # should not raise

    def test_require_capability_unknown(self):
        config = E2EConfig()
        with pytest.raises(pytest.skip.Exception):
            require_capability(config, "storage_class")

    def test_require_capability_missing(self):
        from e2e_harness.core.config import Capabilities

        config = E2EConfig(capabilities=Capabilities(known=True, data={"other": "val"}))
        with pytest.raises(pytest.skip.Exception):
            require_capability(config, "storage_class")

    def test_require_capability_present(self):
        from e2e_harness.core.config import Capabilities

        config = E2EConfig(
            capabilities=Capabilities(known=True, data={"storage_class": "gp3"})
        )
        require_capability(config, "storage_class")  # should not raise
