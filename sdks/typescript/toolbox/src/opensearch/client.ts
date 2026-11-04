import { awaitWithSignal } from "../connection/acquire";
import type { Client as ManagedClient } from "../client";
import type { ClientLifecycle } from "../datasource";
import { Client, Connection, errors } from "@opensearch-project/opensearch";
import { Transport, isConnectionNetworkError } from "../transport";
import { ToolboxError, TimeoutError } from "../errors";
import type { SearchEngine, SearchQuery, SearchResult } from "./types";
import { isIP } from "node:net";

/** IP targets retain their address identity but never send an IP literal as TLS SNI. */
export function openSearchServername(host?: string): string | undefined {
  if (!host) return undefined;
  const unwrapped = host.startsWith("[") && host.endsWith("]") ? host.slice(1, -1) : host;
  return isIP(unwrapped) ? undefined : host;
}

export interface OpenSearchAuth {
  username?: string;
  password?: string;
}

export interface OpenSearchOptions {
  node: string;
  /** Bounded operational facts; never protocol bodies or credentials. */
  onDiagnostic?: (line: string) => void;
  auth: OpenSearchAuth;
  requestTimeoutMs?: number;
  /** Per-route mapping and probe budget; query deadlines remain independent. */
  connectTimeoutMs?: number;
  /** Total acquisition budget; defaults to the per-route budget times the route count. */
  acquireTimeoutMs?: number;
  /** Original TLS identity when a transport maps the socket to localhost. */
  servername?: string;
  /** Only endpoints without an explicit scheme permit HTTP/HTTPS discovery. */
  preferredScheme?: "http" | "https";
  /** Original HTTP authority when dialing a forwarded address. */
  authority?: string;
}

export interface OpenSearchClientApi {
  count(params: Record<string, unknown>): Promise<{ body: { count: number } }>;
  search(params: Record<string, unknown>): Promise<{ body: SearchResult }>;
  ping(params?: Record<string, unknown>, options?: { requestTimeout?: number }): Promise<unknown> & { abort?(): void };
  transport?: {
    request(params: {
      method: string;
      path: string;
      querystring?: Record<string, unknown>;
    }): Promise<{ body: unknown }>;
  };
  close(): Promise<void>;
}

/** Safe error-chain facts for logs; driver messages/meta can contain credentials and response bodies. */
export function describeOpenSearchError(error: unknown): string {
  const parts: string[] = [];
  const seen = new Set<unknown>();
  while (error && typeof error === "object" && !seen.has(error) && parts.length < 4) {
    seen.add(error);
    const value = error as { name?: unknown; code?: unknown; kind?: unknown; statusCode?: unknown; meta?: { statusCode?: unknown }; cause?: unknown; message?: unknown };
    const safe = (value: unknown) => typeof value === "number" ? String(value)
      : typeof value === "string" && /^[a-zA-Z0-9_.-]{1,80}$/.test(value) ? value : undefined;
    const argument = (value.code === "ERR_INVALID_ARG_VALUE" || value.code === "ERR_INVALID_ARG_TYPE")
      && typeof value.message === "string"
      ? value.message.match(/^The ["']([a-zA-Z][a-zA-Z0-9_.]{0,79})["'] (?:argument|option|property)/)?.[1]
        ?? value.message.match(/^The (?:argument|option|property) ["']([a-zA-Z][a-zA-Z0-9_.]{0,79})["']/)?.[1]
      : undefined;
    parts.push([`error=${safe(value.name) ?? "Error"}`, argument && `argument=${argument}`, safe(value.code) && `code=${safe(value.code)}`,
      safe(value.kind) && `kind=${safe(value.kind)}`,
      safe(value.statusCode ?? value.meta?.statusCode) && `status=${safe(value.statusCode ?? value.meta?.statusCode)}`].filter(Boolean).join(" "));
    error = value.cause;
  }
  return parts.join(" cause=") || "error=UnknownError";
}

export interface OpenSearchLifecycle extends ClientLifecycle {
  onDiagnostic?: (line: string) => void;
}

/** OpenSearch-specific read APIs stay outside the engine-neutral SearchEngine contract. */
export interface OpenSearchReadApi extends SearchEngine {
  request(path: string, query?: SearchQuery): Promise<unknown>;
}

export function isOpenSearchReadApi(search: SearchEngine): search is OpenSearchReadApi {
  return typeof (search as Partial<OpenSearchReadApi>).request === "function";
}

/** Routed TLS identity must reach the request, including runtimes that do not forward Agent TLS options. */
class OpenSearchConnection extends Connection {
  /**
   * @spec Synchronous request construction failures balance the SDK's request accounting.
   * @why OpenSearch JS 3.6.0 increments before constructing the request; an exception otherwise leaves close polling forever.
   * @case id=opensearch_sync_request_exit,desc=`TLS construction throws`,expect=`Close completes and process exits naturally`
   */
  override request(params: Parameters<Connection["request"]>[0], callback: Parameters<Connection["request"]>[1]) {
    let completed = false;
    try {
      return super.request(params, (error, response) => { completed = true; callback(error, response); });
    } catch (error) {
      if (!completed) this._openRequests--;
      throw error;
    }
  }
  override buildRequestObject(params: Record<string, unknown>) {
    return { ...super.buildRequestObject(params), servername: openSearchServername(this.ssl?.servername) };
  }
}

function createClient(options: OpenSearchOptions): OpenSearchClientApi {
  return new Client({
    node: options.node,
    Connection: OpenSearchConnection,
    headers: options.authority ? { host: options.authority } : undefined,
    auth: options.auth.username && options.auth.password
      ? { username: options.auth.username, password: options.auth.password }
      : undefined,
    ssl: { rejectUnauthorized: false, servername: openSearchServername(options.servername) },
    requestTimeout: options.requestTimeoutMs ?? 60_000,
    maxRetries: 0,
    // Bun may retry a failed reused socket below the driver even with maxRetries=0.
    agent: { keepAlive: !process.versions.bun },
  }) as unknown as OpenSearchClientApi;
}

/** Official OpenSearch client behind the engine-neutral SearchEngine contract. */
export class OpenSearchEngine implements OpenSearchReadApi {
  private readonly client: OpenSearchClientApi;
  readonly #requests = new Set<Promise<unknown> & { abort?(): void }>();
  #closed = false;

  constructor(private readonly options: OpenSearchOptions, client?: OpenSearchClientApi) {
    this.client = client ?? createClient(options);
  }

  #run<T>(start: () => Promise<T> & { abort?(): void }): Promise<T> & { abort?(): void } {
    if (this.#closed) throw new Error("OpenSearch engine is closed");
    const request = start();
    this.#requests.add(request);
    void request.then(() => this.#requests.delete(request), () => this.#requests.delete(request));
    return request;
  }

  async count(index: string, query: SearchQuery): Promise<number> {
    const response = await this.#run(() => this.client.count({ index, body: { query } }));
    return Number(response.body.count ?? 0);
  }

  async search(index: string, body: SearchQuery): Promise<SearchResult> {
    const response = await this.#run(() => this.client.search({ index, body }));
    return response.body;
  }

  async request(path: string, query?: SearchQuery): Promise<unknown> {
    if (!this.client.transport) throw new Error("OpenSearch client 不支持通用只读请求");
    const response = await this.#run(() => this.client.transport!.request({
      method: "GET",
      path,
      querystring: query,
    }));
    return response.body;
  }

  /** @spec Probe deadlines abort the underlying request even when the driver stalls. */
  async ping(signal?: AbortSignal, timeoutMs = this.options.requestTimeoutMs ?? 60_000): Promise<unknown> {
    signal?.throwIfAborted();
    const started = Date.now();
    const url = new URL(this.options.node);
    const target = `${url.protocol}//${url.host}`;
    const log = this.options.onDiagnostic ?? (() => {});
    log(`OpenSearch probe start target=${target} timeout_ms=${timeoutMs}`);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new TimeoutError(`OpenSearch probe timed out after ${timeoutMs}ms`)), timeoutMs);
    const active = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;
    try {
      const request = this.#run(() => this.client.ping({}, { requestTimeout: timeoutMs }));
      const result = await awaitWithSignal(request, active, () => request.abort?.());
      log(`OpenSearch probe ok target=${target} elapsed_ms=${Date.now() - started}`);
      return result;
    } catch (error) {
      log(`OpenSearch probe failed target=${target} elapsed_ms=${Date.now() - started} ${describeOpenSearchError(error)}`);
      throw error;
    } finally { clearTimeout(timer); }
  }

  async close(): Promise<void> {
    this.#closed = true;
    for (const request of this.#requests) request.abort?.();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new TimeoutError("OpenSearch close timed out after 2000ms")), 2_000);
    try { await awaitWithSignal(this.client.close(), controller.signal); }
    catch (error) {
      this.options.onDiagnostic?.(`OpenSearch close failed ${describeOpenSearchError(error)}`);
      throw error;
    } finally { clearTimeout(timer); }
  }

}

function openSearchRetryReason(error: unknown): string | undefined {
  if (isConnectionNetworkError(error)) return String((error as NodeJS.ErrnoException).code);
  // The driver drops errno when wrapping DNS/socket failures in ConnectionError.
  if (error instanceof errors.TimeoutError) return "ETIMEDOUT";
  if (error instanceof errors.ConnectionError) return error.name;
  return undefined;
}

/**
 * @spec Only connection preparation may advance to another Transport; reads are never replayed.
 * @why The official client connects lazily and wraps socket errors, so creating it does not prove reachability.
 * @case id=opensearch_connect_fallback,desc=`Host cannot reach target`,expect=`Probe next TCP route`,forbid=`Replay a failed search`
 */
export class OpenSearchClient implements ManagedClient, OpenSearchReadApi {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  #initialization?: Promise<void>;
  #disposal?: Promise<void>;
  #engine?: OpenSearchEngine;
  #node?: string;
  get node(): string | undefined { return this.#node; }
  readonly #cleanupErrors: unknown[] = [];
  readonly #operations = new Set<Promise<unknown>>();

  constructor(private readonly configuration: OpenSearchOptions | (() => Promise<OpenSearchOptions>), private readonly transport: Transport, private readonly lifecycle: OpenSearchLifecycle = {}) {
    this.signal = lifecycle.signal ? AbortSignal.any([lifecycle.signal, this.#controller.signal]) : this.#controller.signal;
  }
  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= (async () => {
      const target = await awaitWithSignal((typeof this.configuration === "function" ? this.configuration() : Promise.resolve(this.configuration)), this.signal);
      this.signal.throwIfAborted();
      const explicit = target.node.includes("://");
      const preferred = target.preferredScheme ?? "http";
      const original = new URL(explicit ? target.node : `${preferred}://${target.node}`);
      if (!explicit && !original.port) original.port = "9200";
      const schemes = explicit ? [original.protocol] : preferred === "https" ? ["https:", "http:"] : ["http:", "https:"];
      try {
        this.#engine = await this.transport.acquire({
          name: "OpenSearch connection", signal: this.signal,
          connectTimeoutMs: target.connectTimeoutMs ?? target.requestTimeoutMs ?? 10_000,
          acquireTimeoutMs: target.acquireTimeoutMs, onRoute: this.lifecycle.onRoute,
          open: async (transport, attempt) => {
            if (transport.kind !== "tcp") throw new Error("OpenSearch requires a TCP transport");
            this.lifecycle.onDiagnostic?.(`OpenSearch route start transport=${transport.name} target=${original.protocol}//${original.host} timeout_ms=${attempt.remainingMs()}`);
            const endpoint = await transport.connect({ host: original.hostname,
              port: Number(original.port || (original.protocol === "https:" ? 443 : 80)) }, attempt);
            attempt.signal.throwIfAborted();
            const url = new URL(original);
            url.hostname = endpoint.host;
            url.port = String(endpoint.port);
            for (const [index, scheme] of schemes.entries()) {
              url.protocol = scheme;
              const engine = new OpenSearchEngine({ ...target, onDiagnostic: this.lifecycle.onDiagnostic,
                node: url.toString(), authority: original.host, servername: target.servername ?? original.hostname });
              try {
                await engine.ping(attempt.signal, attempt.remainingMs());
                const selected = new URL(original); selected.protocol = scheme;
                this.#node = selected.toString().replace(/\/$/, "");
                return { connection: engine, transport: transport.name };
              } catch (error) {
                try { await engine.close(); }
                catch (cleanup) {
                  this.#cleanupErrors.push(cleanup);
                  throw new AggregateError([error, cleanup], "OpenSearch initialization and cleanup failed", { cause: error });
                }
                attempt.signal.throwIfAborted();
                if (index === schemes.length - 1 || openSearchRetryReason(error) === undefined) throw error;
              }
            }
            throw new Error("OpenSearch has no protocol candidate");
          },
          close: engine => engine.close(),
          retryReason: openSearchRetryReason,
        });
      } catch (error) {
        this.lifecycle.onDiagnostic?.(`OpenSearch connect failed ${describeOpenSearchError(error)}`);
        this.signal.throwIfAborted();
        // @spec Shared acquisition deadlines retain their timeout classification.
        if (error instanceof ToolboxError && error.kind === "timeout") throw error;
        const reason = openSearchRetryReason(error);
        if (reason === undefined) throw error;
        throw new ToolboxError(`OpenSearch connect failed (${reason})`, { kind: "connection_failed", code: reason, cause: error });
      }
    })();
  }
  #ready(): OpenSearchEngine {
    this.signal.throwIfAborted();
    if (!this.#engine) throw new Error("OpenSearch client is not initialized");
    return this.#engine;
  }
  #run<T>(operation: (engine: OpenSearchEngine) => Promise<T>): Promise<T> {
    const engine = this.#ready();
    const pending = operation(engine);
    this.#operations.add(pending);
    void pending.then(() => this.#operations.delete(pending), () => this.#operations.delete(pending));
    return pending;
  }
  count(index: string, query: SearchQuery) { return this.#run(engine => engine.count(index, query)); }
  search(index: string, body: SearchQuery) { return this.#run(engine => engine.search(index, body)); }
  request(path: string, query?: SearchQuery) { return this.#run(engine => engine.request(path, query)); }
  ping() { return this.#run(engine => engine.ping()); }
  close(): Promise<void> { return this.dispose(); }
  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("OpenSearch client disposed"));
      await this.#initialization?.catch(() => {});
      await this.#engine?.close();
      await Promise.allSettled(this.#operations);
      if (this.#cleanupErrors.length) throw new AggregateError(this.#cleanupErrors, "OpenSearch cleanup failed");
    })();
  }
}

export async function openOpenSearch(configuration: OpenSearchOptions | (() => Promise<OpenSearchOptions>), transport: Transport, lifecycle: OpenSearchLifecycle = {}): Promise<OpenSearchClient> {
  const client = new OpenSearchClient(configuration, transport, lifecycle);
  try { await client.initialize(); }
  catch (error) {
    try { await client.dispose(); }
    catch (cleanup) { throw new AggregateError([error, cleanup], "OpenSearch initialization and cleanup failed", { cause: error }); }
    throw error;
  }
  lifecycle.onDispose?.(() => client.dispose());
  return client;
}
