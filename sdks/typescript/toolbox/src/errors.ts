/** Stable toolbox failure semantics; native causes are for debugging, not user reports. */
export type ErrorKind =
  | "connection_failed" | "connection_lost" | "tls_verification_failed"
  | "authentication_failed" | "permission_denied" | "resource_not_found"
  | "timeout" | "limit_exceeded" | "invalid_response" | "operation_failed"
  | "invalid_argument" | "unsupported_operation";

export class ToolboxError extends Error {
  readonly kind: ErrorKind;
  readonly code?: number | string;
  constructor(message: string, options: { kind: ErrorKind; code?: number | string; cause?: unknown }) {
    super(message, { cause: options.cause });
    this.name = new.target.name;
    this.kind = options.kind;
    this.code = options.code;
  }
}

export class KubernetesError extends ToolboxError {}

export function kubernetesHttpError(status: number): KubernetesError {
  const kind: ErrorKind = ({
    401: "authentication_failed", 403: "permission_denied", 404: "resource_not_found",
    408: "timeout", 429: "limit_exceeded", 504: "timeout",
  } as Record<number, ErrorKind>)[status] ?? "operation_failed";
  return new KubernetesError("Kubernetes resource request failed", { kind, code: status });
}
