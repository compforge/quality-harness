"""Connection evidence is client state, separate from the exception contract."""

from copy import deepcopy

from harness_toolbox.errors import ToolboxError


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

    def snapshot(self) -> dict:
        return deepcopy(
            {
                "protocol": self.protocol,
                "target": self.target,
                "selected_transport": self.selected_transport,
                "attempts": self.attempts,
            }
        )

    def failed(self, transport: str, error: ToolboxError) -> None:
        self.attempts.append(
            {
                "transport": transport,
                "status": "error",
                "error": {"kind": error.kind, "code": error.code, "message": error.message},
            }
        )

    def connected(self, transport: str) -> None:
        self.attempts.append({"transport": transport, "status": "ok"})
        self.selected_transport = transport
