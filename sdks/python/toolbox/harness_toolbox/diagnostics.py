"""Safe access evidence without changing native exception or retry semantics.

Only explicit target fields, exception types and numeric codes are exported. Never
serialize exceptions, requests, connection keys, transport reprs or query payloads.
"""

from __future__ import annotations

import errno
import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy


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


def error_details(error: BaseException) -> dict:
    """Return bounded, JSON-safe diagnostics, including wrapped driver failures.

    Business record absence is not an access failure. Callers interpret successful
    empty queries themselves. Native exceptions remain available to programmatic
    handlers, but their text is deliberately absent from this presentation API.
    """
    causes = []
    pending = [error]
    seen: set[int] = set()
    access = None
    kinds = []
    while pending and len(causes) < 16:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if access is None:
            access = getattr(current, "_toolbox_access", None)
        item = {"type": type(current).__name__}
        code = current.args[0] if current.args else None
        if type(code) is int:
            item["code"] = code
        status = getattr(getattr(current, "response", None), "status_code", None)
        if type(status) is int:
            item["http_status"] = status
            kinds.append(
                {401: "authentication_failed", 403: "permission_denied"}.get(status, "http_error")
            )
        # Some HTTP backends retain only the OpenSSL marker, not the SSL cause.
        # Inspect string args for classification, but never emit them.
        if isinstance(current, ssl.SSLCertVerificationError) or any(
            isinstance(arg, str) and "CERTIFICATE_VERIFY_FAILED" in arg for arg in current.args
        ):
            kinds.append("tls_verification_failed")
        elif isinstance(current, TimeoutError) or type(current).__name__ in (
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
        ):
            kinds.append("timeout")
        elif (
            isinstance(current, ConnectionError)
            or type(current).__name__ == "ConnectError"
            or (
                isinstance(current, OSError)
                and current.errno
                in (errno.ECONNREFUSED, errno.ECONNRESET, errno.ENETUNREACH, errno.EHOSTUNREACH)
            )
        ):
            kinds.append("connection_failed")
        causes.append(item)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        original = getattr(current, "orig", None)
        if isinstance(original, BaseException):
            pending.append(original)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)
    if access and access["protocol"] == "mysql":
        mysql_kinds = {
            1044: "permission_denied",
            1045: "authentication_failed",
            1049: "database_not_found",
            1146: "table_not_found",
            2002: "connection_failed",
            2003: "connection_failed",
            2005: "connection_failed",
            2006: "connection_lost",
            2013: "connection_lost",
        }
        kinds.extend(
            mysql_kinds[item["code"]] for item in causes if item.get("code") in mysql_kinds
        )
    priority = (
        "tls_verification_failed",
        "authentication_failed",
        "permission_denied",
        "database_not_found",
        "table_not_found",
        "timeout",
        "connection_lost",
        "connection_failed",
        "http_error",
    )
    result = {
        "kind": next((kind for kind in priority if kind in kinds), "query_error"),
        "causes": causes,
    }
    if access is not None:
        result["access"] = deepcopy(access)
    if pending:
        result["causes_truncated"] = True
    return result


class _AccessRecorder:
    """Client-owned state; callers receive detached snapshots, never this recorder."""

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

    def annotate(self, error: Exception, stage: str) -> None:
        # Preserve original types/causes so existing exception handlers keep working.
        error._toolbox_access = {**self.snapshot(), "stage": stage}

    @contextmanager
    def operation(self, stage: str) -> Iterator[None]:
        try:
            yield
        except Exception as error:
            self.annotate(error, stage)
            raise

    def failed(self, transport: str, error: Exception) -> None:
        self.annotate(error, "connect")
        details = error_details(error)
        details.pop("access", None)
        self.attempts.append({"transport": transport, "status": "error", "error": details})
        self.annotate(error, "connect")

    def connected(self, transport: str) -> None:
        self.attempts.append({"transport": transport, "status": "ok"})
        self.selected_transport = transport
