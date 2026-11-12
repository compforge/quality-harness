"""Execute protocol-specific Python in an explicitly selected existing Pod."""

import json
from dataclasses import dataclass

from harness_toolbox.transport.kubernetes import KubernetesAccess


@dataclass(frozen=True)
class PodPythonTransport:
    access: KubernetesAccess
    pod: str
    python: str = "python3"
    container: str | None = None

    @property
    def key(self) -> str:
        return repr(self)

    async def run(
        self,
        script: str,
        payload: object,
        *,
        timeout_s: float = 60,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> bytes:
        # Credentials and SQL travel over stdin, never process arguments or a shell.
        return await self.access.execute(
            self.pod,
            [self.python, "-c", script],
            stdin=json.dumps(payload).encode(),
            container=self.container,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        )
