import { httpRequestDiagnostics, type HttpRequestDiagnostics } from "../http/diagnostics";
import { ToolboxError, type ErrorKind } from "../errors";
import type { S3ResponseMetadata } from "./types";

export interface S3ErrorDetails {
  kind: ErrorKind;
  code: string;
  message: string;
  httpStatus?: number;
  requestId?: string;
  extendedRequestId?: string;
  attempts?: number;
  request?: HttpRequestDiagnostics;
}

function identifier(value: unknown): string | undefined {
  return typeof value === "string" && /^[a-zA-Z0-9_.:/+=-]{1,200}$/.test(value) ? value : undefined;
}

function errorKind(code: string, status?: number): ErrorKind {
  if (["InvalidAccessKeyId", "SignatureDoesNotMatch", "ExpiredToken", "InvalidToken", "RequestExpired",
    "RequestTimeTooSkewed", "AuthorizationHeaderMalformed"].includes(code) || status === 401) return "authentication_failed";
  if (code === "AccessDenied" || status === 403) return "permission_denied";
  if (["NoSuchBucket", "NoSuchKey", "NoSuchVersion", "NotFound"].includes(code) || status === 404) return "resource_not_found";
  if (["ETIMEDOUT", "TimeoutError", "RequestTimeout"].includes(code) || status === 408 || status === 504) return "timeout";
  if (["SlowDown", "Throttling", "TooManyRequests"].includes(code) || status === 429) return "limit_exceeded";
  if (["CERT_HAS_EXPIRED", "DEPTH_ZERO_SELF_SIGNED_CERT", "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
    "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "ERR_TLS_CERT_ALTNAME_INVALID"].includes(code)) return "tls_verification_failed";
  if (["ECONNRESET", "EPIPE"].includes(code)) return "connection_lost";
  if (["ENOTFOUND", "EAI_AGAIN", "ECONNREFUSED", "ENETUNREACH", "EHOSTUNREACH"].includes(code)) return "connection_failed";
  return "operation_failed";
}

/**
 * @spec S3 failures retain protocol facts and a native cause; safe messages and JSON never include SDK messages or signed URLs.
 * @spec HEAD 403/404 remain failures, not proof that a particular object is absent.
 */
export class S3Error extends ToolboxError {
  override readonly code: string;
  readonly httpStatus?: number;
  readonly requestId?: string;
  readonly extendedRequestId?: string;
  /** Retained for consumers already reading SDK response metadata. */
  readonly $metadata: S3ResponseMetadata;

  constructor(options: { code?: string; httpStatus?: number; requestId?: string; extendedRequestId?: string;
    attempts?: number; kind?: ErrorKind; cause?: unknown } = {}) {
    const code = identifier(options.code) ?? "S3Error";
    const httpStatus = Number.isInteger(options.httpStatus) && options.httpStatus! >= 100 && options.httpStatus! <= 599 ? options.httpStatus : undefined;
    super(`S3 request failed (${code}${httpStatus ? `, HTTP ${httpStatus}` : ""})`, {
      kind: options.kind ?? errorKind(code, httpStatus), code, cause: options.cause,
    });
    this.code = code;
    this.httpStatus = httpStatus;
    this.requestId = identifier(options.requestId);
    this.extendedRequestId = identifier(options.extendedRequestId);
    this.$metadata = { httpStatusCode: httpStatus, requestId: this.requestId, extendedRequestId: this.extendedRequestId,
      attempts: Number.isSafeInteger(options.attempts) && options.attempts! > 0 ? options.attempts : undefined };
  }

  static from(error: unknown): S3Error {
    if (error instanceof S3Error) return error;
    const value = error && typeof error === "object" ? error as { code?: unknown; name?: unknown; $metadata?: S3ResponseMetadata } : {};
    return new S3Error({ code: identifier(value.code) ?? identifier(value.name),
      httpStatus: value.$metadata?.httpStatusCode, requestId: value.$metadata?.requestId,
      extendedRequestId: value.$metadata?.extendedRequestId, attempts: value.$metadata?.attempts,
      kind: error instanceof ToolboxError ? error.kind : undefined, cause: error });
  }

  toJSON(): S3ErrorDetails {
    return { kind: this.kind, code: this.code, message: this.message, httpStatus: this.httpStatus,
      requestId: this.requestId, extendedRequestId: this.extendedRequestId, attempts: this.$metadata.attempts,
      request: httpRequestDiagnostics(this) };
  }
}
