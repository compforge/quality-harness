"""Async evidence source: select unique trace IDs, fetch a complete projected skeleton,
and read fields through stable span references. Environment and credential resolution
belong to the consumer; backends own HTTP and storage details.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from trace_harness.loading.model import EvidenceRef
from trace_harness.model.span import NormSpan


@dataclass
class SpanQuery:
    """后端无关的「选择包含匹配 span 的 trace」。各 Source 翻译成自己后端的查询。

    `attr_eq` 是 nested-tag 等值过滤（如 `{"error.type": "ModelTotalTimeoutError"}`）——
    域 Selector（trace-as 的 error_code）把语义条件编译成它。
    """

    attr_eq: dict[str, str] = field(default_factory=dict)
    trace_ids: list[str] | None = None  # 直接圈定 trace（seed / 已知 id 列表）
    error_only: bool = False
    service: str | None = None
    since_ms: float | None = None
    until_ms: float | None = None
    limit: int = 1000
    order: Literal["trace_id", "latest"] = "trace_id"
    operation_names: list[str] | None = None
    # latest orders unique traces by their newest matching span start, ties by trace ID.


@runtime_checkable
class Source(Protocol):
    """Async evidence access. The caller owns the source's lifetime.

    ``fields=None`` requests full documents; a tuple requests only those tag keys.
    Selection yields unique trace IDs, not partial traces masquerading as trees.
    """

    namespace: str

    def select(self, query: SpanQuery) -> AsyncIterator[str]: ...

    async def fetch(self, trace_id: str, fields: tuple[str, ...]) -> dict[str, NormSpan]: ...

    async def read(
        self, refs: tuple[EvidenceRef, ...], fields: tuple[str, ...] | None
    ) -> dict[str, NormSpan]: ...

    async def aclose(self) -> None: ...
