"""Connection evidence is client state, separate from the exception contract."""

from copy import deepcopy
from dataclasses import asdict

from harness_toolbox.errors import ToolboxError
from harness_toolbox.transport import Endpoint


def _transport_name(transport: object) -> str:
    from harness_toolbox.transport import DirectTransport, PodPythonTransport, PortForwardTransport

    for kind, name in (
        (PodPythonTransport, "pod-python"),
        (PortForwardTransport, "port-forward"),
        (DirectTransport, "direct"),
    ):
        if isinstance(transport, kind):
            return name
    return type(transport).__name__


class _AccessRecorder:
    """Client-owned state; callers receive detached snapshots."""

    def __init__(self, protocol: str) -> None:
        self.protocol = protocol
        self.target: dict | None = None
        self.attempts: list[dict] = []
        self.selected_transport: str | None = None
        self.selected_endpoint: dict | None = None

    def snapshot(self) -> dict:
        return deepcopy(
            {
                "protocol": self.protocol,
                "target": self.target,
                "selected_transport": self.selected_transport,
                "selected_endpoint": self.selected_endpoint,
                "attempts": self.attempts,
            }
        )

    def failed(
        self,
        transport: str,
        error: ToolboxError,
        endpoint: Endpoint | None = None,
        source: str = "configured",
    ) -> None:
        self.attempts.append(
            {
                "transport": transport,
                "endpoint": asdict(endpoint) if endpoint else None,
                "source": source,
                "status": "error",
                "error": {"kind": error.kind, "code": error.code, "message": error.message},
            }
        )

    def connected(
        self, transport: str, endpoint: Endpoint | None = None, source: str = "configured"
    ) -> None:
        self.selected_endpoint = asdict(endpoint) if endpoint else None
        self.attempts.append(
            {
                "transport": transport,
                "endpoint": self.selected_endpoint,
                "source": source,
                "status": "ok",
            }
        )
        self.selected_transport = transport
