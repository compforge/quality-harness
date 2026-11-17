"""Metric database access options and query facts, independent of perf semantics."""

from dataclasses import dataclass
from typing import Literal, Protocol

MetricDBKind = Literal["prometheus", "victoriametrics"]


@dataclass(frozen=True)
class MetricDBOptions:
    timeout_ms: int = 5_000
    max_response_bytes: int = 16 * 1024 * 1024
    max_series: int = 20_000
    connection_pool_maxsize: int = 8


@dataclass(frozen=True)
class MetricDBResult:
    """Native data plus server annotations; consumers decide whether warnings are acceptable."""

    data: dict
    warnings: tuple[str, ...] = ()
    infos: tuple[str, ...] = ()
    is_partial: bool | None = None


class MetricDBBackend(Protocol):
    """Backend wire behavior; HTTP ownership and RED meaning stay outside adapters."""

    def query_parameters(self) -> dict[str, str]: ...
