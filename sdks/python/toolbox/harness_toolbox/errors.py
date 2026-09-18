"""Public failure contract; protocol adapters own native exception translation."""

from enum import StrEnum


class ErrorKind(StrEnum):
    INVALID_ARGUMENT = "invalid_argument"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    CONNECTION_FAILED = "connection_failed"
    CONNECTION_LOST = "connection_lost"
    TLS_VERIFICATION_FAILED = "tls_verification_failed"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    TIMEOUT = "timeout"
    LIMIT_EXCEEDED = "limit_exceeded"
    INVALID_RESPONSE = "invalid_response"
    OPERATION_FAILED = "operation_failed"


class ToolboxError(Exception):
    """A safe message, stable kind and optional protocol-native code.

    why: Consumers catch toolbox types rather than depending on driver exceptions.
    Messages must omit credentials and query payloads; native exceptions belong in
    ``__cause__`` for debugging, not in user-facing reports. Serialization and retry
    decisions belong to callers, not to this exception contract.
    """

    def __init__(self, message: str, *, kind: ErrorKind, code: int | str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.code = code


class MySQLError(ToolboxError):
    """MySQL access failed."""


class KubernetesError(ToolboxError):
    """Kubernetes discovery or access failed."""


class MySQLConnectionError(MySQLError):
    """Connection acquisition or probing failed, before user SQL execution."""


class MySQLQueryError(MySQLError):
    """Query execution or result collection failed; side effects may have occurred."""


class OpenSearchError(ToolboxError):
    """OpenSearch access failed."""


class OpenSearchConnectionError(OpenSearchError):
    """Connection acquisition or probing failed, before the user request."""


class OpenSearchRequestError(OpenSearchError):
    """Request execution or response collection failed."""


class PrometheusQueryError(ToolboxError):
    """Remote Prometheus query failed or exceeded its response budget."""
