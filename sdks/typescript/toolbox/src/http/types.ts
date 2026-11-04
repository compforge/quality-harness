export interface HttpTarget {
  /** Logical endpoint; forwarding never changes HTTP Host or TLS identity. */
  endpoint: string;
  ca?: string;
}

/** Request time includes queueing, route preparation and response consumption. */
export interface HttpLimits {
  concurrency: number;
  connectTimeoutMs: number;
  requestTimeoutMs: number;
  maxResponseBytes: number;
}

export interface HttpRequestOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: string | Uint8Array;
  signal?: AbortSignal;
}

/** The wire body is consumed or destroyed before return; callers own no socket. */
export interface HttpResponse {
  status: number;
  headers: Readonly<Record<string, string>>;
  body: Uint8Array;
}

/**
 * @spec Responses are bounded and consumed before returning; cancellation releases the request's resources.
 * @rule HTTP statuses and redirects are returned without automatic replay or redirect following.
 * @case id=http_forwarded_identity,desc=`HTTP through a mapped route`,expect=`original Host and TLS identity`
 * @case id=http_cancel_body,desc=`cancel an active body read`,expect=`original cancellation reason and released pool slot`
 * @see {@link component://docs/toolbox.md}
 */
export interface HttpApi {
  request(path: string, options?: HttpRequestOptions): Promise<HttpResponse>;
}
