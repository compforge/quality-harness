"""Runner base class and Outcome data structure.

Runner is the protocol adapter: it takes a Request, triggers the service,
and returns an Outcome. Different runners handle different protocols
(JSON, SSE, WebSocket, etc.) but all produce the same Outcome structure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from harness_common import Outcome as BaseOutcome


@dataclass
class Request:
    """What to send to the service."""

    method: str = "POST"
    path: str = ""
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    exclude_headers: set[str] = field(default_factory=set)


@dataclass
class Outcome(BaseOutcome):
    """Standardized runner output — the contract between Runner and Judge.

    All runners produce Outcome regardless of protocol. Judge reads Outcome
    without knowing whether it came from a JSON response or an SSE stream.
    """

    status_code: int = 0
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: bytes = b""

    def field(self, path: str) -> Any:
        """Dot-path access into body. E.g. field("sandbox_name") or field("data.items.0.id")."""
        if self.body is None:
            return None
        parts = path.split(".")
        current: Any = self.body
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    def field_str(self, path: str) -> str:
        """Dot-path access returning string (empty string if missing/null)."""
        val = self.field(path)
        if val is None:
            return ""
        return str(val)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for artifact persistence (stage resumability)."""
        return {
            "status_code": self.status_code,
            "body": self.body,
            "headers": self.headers,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "raw": self.raw.decode("utf-8", errors="replace") if self.raw else "",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Outcome:
        """Deserialize from artifact."""
        raw_str = data.get("raw", "")
        return cls(
            status_code=data.get("status_code", 0),
            body=data.get("body"),
            headers=data.get("headers", {}),
            duration_ms=data.get("duration_ms", 0),
            metadata=data.get("metadata", {}),
            raw=raw_str.encode("utf-8") if isinstance(raw_str, str) else b"",
        )


class BaseRunner(ABC):
    """Abstract runner — subclasses implement protocol-specific trigger logic."""

    @abstractmethod
    def trigger(self, request: Request) -> Outcome:
        """Send request to the service and return standardized Outcome."""
        ...
