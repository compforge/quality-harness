"""Common low-cardinality failure taxonomy.

Loaders classify source-specific errors; this module only provides shared
vocabulary and constructors for failure families that recur across agents.
"""

from __future__ import annotations

from typing import Literal

from trajectory_harness.model import Failure

LLMFailurePhase = Literal[
    "routing",
    "connection_pool",
    "connect",
    "request_write",
    "request",
    "first_chunk",
    "inter_chunk",
    "response_parse",
]
LLMTimeoutPhase = Literal[
    "routing",
    "connection_pool",
    "connect",
    "request_write",
    "request",
    "first_chunk",
    "inter_chunk",
]
LLMErrorType = Literal[
    "timeout",
    "rate_limit",
    "client_error",
    "server_error",
    "network_error",
    "invalid_response",
    "unknown",
]


def llm_failure(
    phase: LLMFailurePhase,
    error_type: LLMErrorType,
    *,
    code: str = "",
    message: str = "",
) -> Failure:
    """Build a normalized LLM failure without prescribing source parsing."""

    return Failure(
        kind="llm",
        phase=phase,
        error_type=error_type,
        code=code,
        message=message,
    )


def llm_timeout(
    phase: LLMTimeoutPhase,
    *,
    code: str = "",
    message: str = "",
) -> Failure:
    """Build an LLM timeout at the most specific observed progress boundary.

    Loaders should use ``request`` when the source exposes only an overall
    deadline. They must not infer ``first_chunk`` or ``inter_chunk`` from a
    non-streaming response that provides no chunk-level progress.
    """

    return llm_failure(phase, "timeout", code=code, message=message)
