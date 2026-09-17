"""Kubernetes policy without importing optional drivers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Options:
    """Namespace and explicit Kubernetes API resource limits."""

    namespace: str
    request_timeout_s: float
    connection_pool_maxsize: int
    exec_timeout_s: float = 60
    exec_concurrency: int = 4
    max_exec_bytes: int = 64 * 1024 * 1024
