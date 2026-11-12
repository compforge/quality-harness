"""Explicit kubectl access shared by Kubernetes connection paths."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from harness_toolbox.process import run


@dataclass(frozen=True)
class KubernetesAccess:
    kubeconfig: str | None
    namespace: str
    context: str | None = None
    kubectl: str = "kubectl"

    def command(self, *args: str) -> list[str]:
        command = [self.kubectl, "-n", self.namespace]
        if self.kubeconfig:
            command += ["--kubeconfig", str(Path(self.kubeconfig).expanduser())]
        if self.context:
            command += ["--context", self.context]
        return command + list(args)

    async def execute(
        self,
        pod: str,
        command: Sequence[str],
        *,
        stdin: bytes = b"",
        container: str | None = None,
        timeout_s: float = 60,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> bytes:
        args = ["exec"]
        # Opening then closing an unused stdin stream can truncate remote stdout.
        if stdin:
            args.append("-i")
        args.append(pod)
        if container:
            args += ["-c", container]
        return await run(
            self.command(*args, "--", *command),
            stdin=stdin,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )
