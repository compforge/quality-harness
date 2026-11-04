import { HttpRequest } from "@smithy/core/protocols";
import { Readable } from "node:stream";
import { raceSignal } from "race-signal";
import type { Client } from "../client";
import type { ClientLifecycle } from "../datasource";
import { ConcurrencyPool } from "../concurrency";
import { TimeoutError, ToolboxError } from "../errors";
import { Transport, isConnectionNetworkError } from "../transport";
import { httpHandler } from "./handler";
import type { HttpTarget, HttpLimits, HttpRequestOptions, HttpResponse, HttpApi } from "./types";

/** HTTP clients borrow a Transport; their pools and response bodies belong to the client. */
export class HttpClient implements Client, HttpApi {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  readonly #endpoint: URL;
  readonly #target: HttpTarget;
  readonly #limits: HttpLimits;
  readonly #pool: ConcurrencyPool;
  readonly #operations = new Set<Promise<unknown>>();
  #initialization?: Promise<void>;
  #disposal?: Promise<void>;
  #handler?: ReturnType<typeof httpHandler>;

  constructor(target: HttpTarget, private readonly transport: Transport, limits: HttpLimits,
    private readonly lifecycle: ClientLifecycle = {}) {
    for (const name of ["concurrency", "connectTimeoutMs", "requestTimeoutMs", "maxResponseBytes"] as const) {
      const value = limits[name];
      if (!Number.isSafeInteger(value) || value < 1 || (name.endsWith("Ms") && value > 2_147_483_647)) {
        throw new Error(`${name} must be a positive integer within its supported range`);
      }
    }
    this.#endpoint = new URL(target.endpoint);
    if (!["http:", "https:"].includes(this.#endpoint.protocol) || this.#endpoint.username || this.#endpoint.password
      || this.#endpoint.search || this.#endpoint.hash) throw new Error("HTTP endpoint requires HTTP(S), without credentials, query or fragment");
    this.#target = { ...target };
    this.#limits = { ...limits };
    this.#pool = new ConcurrencyPool(limits.concurrency);
    this.signal = AbortSignal.any([this.#controller.signal, transport.signal, ...(lifecycle.signal ? [lifecycle.signal] : [])]);
  }

  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= (async () => {
      const endpoint = this.#endpoint;
      const route = await this.transport.acquire({ name: "HTTP route", signal: this.signal,
        connectTimeoutMs: this.#limits.connectTimeoutMs, onRoute: this.lifecycle.onRoute,
        open: async (path, attempt) => {
          if (path.kind !== "tcp") throw new Error("HTTP requires a TCP transport");
          return { connection: await path.connect({ host: endpoint.hostname.replace(/^\[|\]$/g, ""),
            port: Number(endpoint.port || (endpoint.protocol === "https:" ? 443 : 80)) }, attempt), transport: path.name };
        },
        close: () => {},
        retryReason: error => isConnectionNetworkError(error) ? String((error as NodeJS.ErrnoException).code) : undefined,
      });
      this.signal.throwIfAborted();
      this.#handler = httpHandler(this.#target, route, this.#limits);
    })();
  }

  request(path: string, options: HttpRequestOptions = {}): Promise<HttpResponse> {
    this.signal.throwIfAborted();
    const url = new URL(path, this.#endpoint);
    if (url.origin !== this.#endpoint.origin || url.username || url.password || url.hash) {
      throw new Error("HTTP request must retain the configured origin and contain no credentials or fragment");
    }
    const headers: Record<string, string> = {};
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      if (name.toLowerCase() === "host") throw new Error("HTTP Host is owned by the configured endpoint");
      headers[name.toLowerCase()] = value;
    }
    headers.host = url.host;
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(new TimeoutError(`HTTP request timed out after ${this.#limits.requestTimeoutMs}ms`)), this.#limits.requestTimeoutMs);
    timer.unref();
    const signal = AbortSignal.any([this.signal, timeout.signal, ...(options.signal ? [options.signal] : [])]);
    const pending = this.#pool.run(async () => {
      // Stop a caller's wait without canceling another caller's shared initialization.
      await raceSignal(this.initialize(), signal);
      signal.throwIfAborted();
      const { response } = await this.#handler!.handle(new HttpRequest({
        protocol: url.protocol, hostname: url.hostname, port: Number(url.port || (url.protocol === "https:" ? 443 : 80)),
        method: options.method ?? "GET", path: url.pathname + url.search, headers, body: options.body,
      }), { abortSignal: signal });
      const body = response.body;
      if (!(body instanceof Readable)) throw new ToolboxError("HTTP response has no readable body", { kind: "invalid_response" });
      const abort = () => body.destroy(new Error("HTTP body read cancelled", { cause: signal.reason }));
      signal.addEventListener("abort", abort, { once: true });
      try {
        signal.throwIfAborted();
        const chunks: Buffer[] = [];
        let size = 0;
        for await (const chunk of body) {
          signal.throwIfAborted();
          const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          size += bytes.length;
          if (size > this.#limits.maxResponseBytes) throw new ToolboxError(
            `HTTP response exceeded ${this.#limits.maxResponseBytes} bytes`, { kind: "limit_exceeded" });
          chunks.push(bytes);
        }
        return { status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks, size) };
      } finally {
        signal.removeEventListener("abort", abort);
        body.destroy();
      }
    }, signal).catch(error => { signal.throwIfAborted(); throw error; }).finally(() => clearTimeout(timer));
    this.#operations.add(pending);
    void pending.then(() => this.#operations.delete(pending), () => this.#operations.delete(pending));
    return pending;
  }

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("HTTP client disposed"));
      await this.#initialization?.catch(() => {});
      await Promise.allSettled(this.#operations);
      this.#handler?.destroy();
    })();
  }
}
