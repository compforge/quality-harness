"""Evidence identity and loading policy; neither carries business classification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRef:
    trace_id: str
    span_id: str
    index: str = ""
    document_id: str = ""


@dataclass(frozen=True)
class LoadConfig:
    lazy: bool = True
    fields: tuple[str, ...] | None = None
    concurrency: int = 8
    active_traces: int = 4
    max_trace_bytes: int = 64 * 1024 * 1024
    cache_bytes: int = 128 * 1024 * 1024

    def __post_init__(self):
        if min(self.concurrency, self.active_traces, self.max_trace_bytes, self.cache_bytes) <= 0:
            raise ValueError("loading budgets must be positive")


class EvidenceMissing(LookupError):
    """An observed storage record is unavailable; this is not an empty field."""


class EvidenceTooLarge(ValueError):
    """One trace exceeds the configured working-set budget."""
