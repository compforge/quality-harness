"""Optional Deployer implementations for perf ResourceProfiles."""

from __future__ import annotations

from harness_common import Deployer

from perf_harness.model import Deployment, ResourceProfile
from perf_harness.sh import run_capture


class HelmDeployer(Deployer[Deployment]):
    """Drive a ResourceProfile via ``helm upgrade`` against a k8s release.

    ``set_paths`` maps ResourceProfile fields to chart value paths so the same
    deployer works across services with different values.yaml shapes; the
    defaults match common service charts (``uvicorn.workers``,
    ``resources.limits.*``). Unknown/None fields are skipped.
    """

    DEFAULT_SET_PATHS = {
        "workers": "uvicorn.workers",
        "memory": "resources.limits.memory",
        "cpu": "resources.limits.cpu",
        "replicas": "replicaCount",
    }

    def __init__(
        self,
        *,
        release: str,
        chart_path: str,
        namespace: str,
        kubeconfig: str,
        base_values: str | None = None,
        set_paths: dict[str, str] | None = None,
        rollout_timeout_s: int = 180,
        extra_set: dict[str, str] | None = None,
    ) -> None:
        self.release = release
        self.chart_path = chart_path
        self.namespace = namespace
        self.kubeconfig = kubeconfig
        self.base_values = base_values
        self.set_paths = set_paths or dict(self.DEFAULT_SET_PATHS)
        self.rollout_timeout_s = rollout_timeout_s
        self.extra_set = extra_set or {}

    def _set_args(self, profile: ResourceProfile) -> list[str]:
        sets: dict[str, str] = {}
        for field_name, path in self.set_paths.items():
            val = getattr(profile, field_name, None)
            if val is not None:
                sets[path] = str(val)
        sets.update(self.extra_set)
        sets.update(profile.extra)
        args: list[str] = []
        for path, val in sets.items():
            args += ["--set", f"{path}={val}"]
        return args

    async def deploy(self, deployment: Deployment) -> None:
        profile = deployment.resources
        cmd = [
            "helm",
            "--kubeconfig",
            self.kubeconfig,
            "-n",
            self.namespace,
            "upgrade",
            self.release,
            self.chart_path,
            "--reuse-values",
        ]
        if self.base_values:
            cmd += ["-f", self.base_values]
        cmd += self._set_args(profile)
        await run_capture(cmd)
        await run_capture(
            [
                "kubectl",
                "--kubeconfig",
                self.kubeconfig,
                "-n",
                self.namespace,
                "rollout",
                "status",
                f"deploy/{self.release}",
                f"--timeout={self.rollout_timeout_s}s",
            ]
        )

    async def teardown(self) -> None:
        cmd = [
            "helm",
            "--kubeconfig",
            self.kubeconfig,
            "-n",
            self.namespace,
            "upgrade",
            self.release,
            self.chart_path,
        ]
        if self.base_values:
            cmd += ["-f", self.base_values]
        await run_capture(cmd)
