"""E2E harness configuration loader.

Reads config.yaml with ${VAR:-default} interpolation, falling back to
environment variables when config.yaml is absent.
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from harness_common import Component, Forge, KubernetesEnvironment, Repository
from harness_common import Experiment as BaseExperiment
from harness_common import Service as BaseService


@dataclass(frozen=True, slots=True)
class Service(BaseService):
    """E2E view of a Service, including HTTP access configuration."""

    component: Component = field(
        default_factory=lambda: Component(
            repository=Repository(forge=Forge(name=""), path=""),
            name="",
        )
    )
    environment: KubernetesEnvironment = field(
        default_factory=lambda: KubernetesEnvironment(name="")
    )
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(kw_only=True)
class Experiment(BaseExperiment):
    """One E2E verification suite executed against a Service."""

    service: Service
    caseset: str


@dataclass
class RuntimeConfig:
    http_timeout_s: int = 120
    poll_interval_ms: int = 500
    poll_timeout_s: int = 60
    parallel: int = 4


@dataclass
class Capabilities:
    known: bool = False
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoverConfigSection:
    """Optional dev-time tooling config for casegen discovery."""

    source_root: str = ""  # dir to scan for @case/@spec (relative to config.yaml dir)
    test_root: str = ""  # where tests land, grouped <test_root>/<group>/ (relative to config.yaml dir)


@dataclass
class E2EConfig:
    service: Service = field(default_factory=lambda: Service(name=""))
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    profile: str = "full"
    capabilities: Capabilities = field(default_factory=Capabilities)
    custom: dict[str, str] = field(default_factory=dict)
    discover: DiscoverConfigSection = field(default_factory=DiscoverConfigSection)


_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate(value: str) -> str:
    """Replace ${VAR} and ${VAR:-default} patterns with environment values."""

    def _replace(m: re.Match) -> str:
        expr = m.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            return os.environ.get(var, default)
        val = os.environ.get(expr)
        if val is None:
            raise ValueError(
                f"environment variable ${{{expr}}} is required but not set"
            )
        return val

    return _ENV_PATTERN.sub(_replace, value)


def _interpolate_recursive(obj: Any) -> Any:
    if isinstance(obj, str):
        return _interpolate(obj)
    if isinstance(obj, dict):
        return {k: _interpolate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_recursive(v) for v in obj]
    return obj


def load_config(config_path: str | Path = "config.yaml") -> E2EConfig:
    """Load config.yaml with environment-variable interpolation or env-only fallback."""
    path = Path(config_path)

    if path.exists():
        raw = yaml.safe_load(path.read_text())
        if raw is None:
            raw = {}
        data = _interpolate_recursive(raw)
    else:
        data = _build_from_env()

    _validate_config(data)
    return _parse_config(data)


def _build_from_env() -> dict:
    """Fallback: construct config dict purely from environment variables."""
    raw_headers = os.environ.get("E2E_SERVICE_HEADERS", "")
    headers = json.loads(raw_headers) if raw_headers else {}
    if not isinstance(headers, dict):
        raise ValueError("E2E_SERVICE_HEADERS must be a JSON object")
    return {
        "service": {
            "name": os.environ.get("E2E_SERVICE_NAME", ""),
            "component": {
                "repository": {
                    "forge": os.environ.get("E2E_FORGE", ""),
                    "path": os.environ.get("E2E_COMPONENT_REPOSITORY", ""),
                },
                "name": os.environ.get("E2E_COMPONENT_NAME", ""),
            },
            "environment": {
                "name": os.environ.get("E2E_ENVIRONMENT", ""),
                "kubeconfig": os.environ.get("E2E_KUBECONFIG", ""),
                "context": os.environ.get("E2E_KUBE_CONTEXT") or None,
            },
            "base_url": os.environ.get("E2E_BASE_URL", ""),
            "headers": headers,
        },
        "runtime": {},
        "profile": os.environ.get("E2E_PROFILE", "full"),
        "custom": {},
    }


def _validate_config(data: Any) -> None:
    root = _require_mapping(data, "config")
    _reject_unknown_fields(
        root,
        "config",
        {
            "service",
            "runtime",
            "profile",
            "capabilities_endpoint",
            "custom",
            "discover",
            "judge",
        },
    )

    service = _mapping_field(root, "service", "service")
    _reject_unknown_fields(
        service,
        "service",
        {"name", "component", "environment", "base_url", "headers"},
    )
    component = _mapping_field(service, "component", "service.component")
    _reject_unknown_fields(component, "service.component", {"repository", "name"})
    repository = _mapping_field(component, "repository", "service.component.repository")
    _reject_unknown_fields(
        repository, "service.component.repository", {"forge", "path"}
    )
    environment = _mapping_field(service, "environment", "service.environment")
    _reject_unknown_fields(
        environment,
        "service.environment",
        {"name", "kubeconfig", "context"},
    )
    _mapping_field(service, "headers", "service.headers")

    runtime = _mapping_field(root, "runtime", "runtime")
    _reject_unknown_fields(
        runtime,
        "runtime",
        {"http_timeout_s", "poll_interval_ms", "poll_timeout_s", "parallel"},
    )
    discover = _mapping_field(root, "discover", "discover")
    _reject_unknown_fields(discover, "discover", {"source_root", "test_root"})
    judge = _mapping_field(root, "judge", "judge")
    _reject_unknown_fields(judge, "judge", {"llm_endpoint", "llm_model", "llm_api_key"})
    _mapping_field(root, "custom", "custom")


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a mapping")
    return value


def _mapping_field(parent: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        return {}
    return _require_mapping(value, path)


def _reject_unknown_fields(data: dict[str, Any], path: str, allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} contains unknown field(s): {', '.join(unknown)}")


def _parse_config(data: dict) -> E2EConfig:
    svc = data.get("service", {})
    rt = data.get("runtime", {})
    custom = data.get("custom", {})
    disc = data.get("discover", {}) or {}

    component = svc.get("component") or {}
    repository = component.get("repository") or {}
    environment = svc.get("environment") or {}
    headers = svc.get("headers") or {}
    return E2EConfig(
        service=Service(
            name=svc.get("name", ""),
            component=Component(
                repository=Repository(
                    forge=Forge(name=str(repository.get("forge", ""))),
                    path=str(repository.get("path", "")),
                ),
                name=str(component.get("name", "")),
            ),
            environment=KubernetesEnvironment(
                name=str(environment.get("name", "")),
                kubeconfig=str(environment.get("kubeconfig", "")),
                context=(
                    str(environment["context"]) if environment.get("context") else None
                ),
            ),
            base_url=svc.get("base_url", "").rstrip("/"),
            headers={str(k): str(v) for k, v in headers.items()} if headers else {},
        ),
        runtime=RuntimeConfig(
            http_timeout_s=int(rt.get("http_timeout_s", 120)),
            poll_interval_ms=int(rt.get("poll_interval_ms", 500)),
            poll_timeout_s=int(rt.get("poll_timeout_s", 60)),
            parallel=int(rt.get("parallel", 4)),
        ),
        profile=data.get("profile", "full"),
        custom={str(k): str(v) for k, v in custom.items()} if custom else {},
        discover=DiscoverConfigSection(
            source_root=str(disc.get("source_root", "")),
            test_root=str(disc.get("test_root", "")),
        ),
    )
