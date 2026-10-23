import { acquireConnection, awaitWithSignal } from "../connection/acquire";
import type { Client as ManagedClient } from "../client";
import type { ConnectionSource, ClientLifecycle } from "../datasource";
import { Client, Connection, errors } from "@opensearch-project/opensearch";
import { isConnectionNetworkError } from "../transport";
import { ToolboxError } from "../errors";
import type { SearchEngine, SearchQuery, SearchResult } from "./types";

export interface OpenSearchAuth {
  username?: string;
  password?: string;
}

export interface OpenSearchOptions {
  node: string;
  auth: OpenSearchAuth;
  requestTimeoutMs?: number;
  /** Per-route mapping and probe budget; query deadlines remain independent. */
  connectTimeoutMs?: number;
  /** Total acquisition budget; defaults to the per-route budget times the route count. */
  acquireTimeoutMs?: number;
  /** Original TLS identity when a transport maps the socket to localhost. */
  servername?: string;
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

/** OpenSearch-specific read APIs stay outside the engine-neutral SearchEngine contract. */
export interface OpenSearchReadApi extends SearchEngine {
  request(path: string, query?: SearchQuery): Promise<unknown>;
}

export function isOpenSearchReadApi(search: SearchEngine): search is OpenSearchReadApi {
  return typeof (search as Partial<OpenSearchReadApi>).request === "function";
}

/** Routed TLS identity must reach the request, including runtimes that do not forward Agent TLS options. */
class OpenSearchConnection extends Connection {
  override buildRequestObject(params: Record<string, unknown>) {
    return { ...super.buildRequestObject(params), servername: this.ssl?.servername };
  }
}

function createClient(options: OpenSearchOptions): OpenSearchClientApi {
  return new Client({
    node: options.node,
    Connection: OpenSearchConnection,
    auth: options.auth.username && options.auth.password
      ? { username: options.auth.username, password: options.auth.password }
      : undefined,
    ssl: { rejectUnauthorized: false, servername: options.servername },
    requestTimeout: options.requestTimeoutMs ?? 60_000,
    maxRetries: 0,
    // Bun may retry a failed reused socket below the driver even with maxRetries=0.
    agent: { keepAlive: !process.versions.bun },
  }) as unknown as OpenSearchClientApi;
}

/** Official OpenSearch client behind the engine-neutral SearchEngine contract. */
export class OpenSearchEngine implements OpenSearchReadApi {
  private readonly client: OpenSearchClientApi;

  constructor(options: OpenSearchOptions, client?: OpenSearchClientApi) {
    this.client = client ?? createClient(options);
  }

  async count(index: string, query: SearchQuery): Promise<number> {
    const response = await this.client.count({ index, body: { query } });
    return Number(response.body.count ?? 0);
  }

  async search(index: string, body: SearchQuery): Promise<SearchResult> {
    const response = await this.client.search({ index, body });
    return response.body;
  }

  async request(path: string, query?: SearchQuery): Promise<unknown> {
    if (!this.client.transport) throw new Error("OpenSearch client 不支持通用只读请求");
    const response = await this.client.transport.request({
      method: "GET",
      path,
      querystring: query,
    });
    return response.body;
  }

  async ping(signal?: AbortSignal, timeoutMs?: number): Promise<unknown> {
    signal?.throwIfAborted();
    const request = this.client.ping({}, timeoutMs === undefined ? undefined : { requestTimeout: timeoutMs });
    const abort = () => { request.abort?.(); };
    signal?.addEventListener("abort", abort, { once: true });
    try { return await request; }
    finally { signal?.removeEventListener("abort", abort); }
  }

  close(): Promise<void> {
    return this.client.close();
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
  readonly #operations = new Set<Promise<unknown>>();

  constructor(private readonly source: ConnectionSource<OpenSearchOptions>, private readonly lifecycle: ClientLifecycle = {}) {
    this.signal = lifecycle.signal ? AbortSignal.any([lifecycle.signal, this.#controller.signal]) : this.#controller.signal;
  }
  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= (async () => {
      const target = await awaitWithSignal(this.source.resolve(), this.signal);
      this.signal.throwIfAborted();
      const original = new URL(target.node);
      try {
        this.#engine = await acquireConnection(this.source.transports, {
          name: "OpenSearch connection", signal: this.signal,
          connectTimeoutMs: target.connectTimeoutMs ?? target.requestTimeoutMs ?? 10_000,
          acquireTimeoutMs: target.acquireTimeoutMs, onRoute: this.lifecycle.onRoute,
          open: async (transport, attempt) => {
            if (transport.kind !== "tcp") throw new Error("OpenSearch requires a TCP transport");
            const endpoint = await transport.connect({ host: original.hostname,
              port: Number(original.port || (original.protocol === "https:" ? 443 : 80)),
              servername: target.servername ?? original.hostname });
            attempt.signal.throwIfAborted();
            const url = new URL(original);
            url.hostname = endpoint.host;
            url.port = String(endpoint.port);
            const engine = new OpenSearchEngine({ ...target, node: url.toString(), servername: endpoint.servername ?? target.servername ?? original.hostname });
            try {
              await engine.ping(attempt.signal, attempt.remainingMs());
              return { connection: engine, transport: transport.name };
            } catch (error) { await engine.close(); throw error; }
          },
          close: engine => engine.close(),
          retryReason: openSearchRetryReason,
        });
      } catch (error) {
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
      await Promise.allSettled(this.#operations);
      await this.#engine?.close();
    })();
  }
}

export async function openOpenSearch(source: ConnectionSource<OpenSearchOptions>, lifecycle: ClientLifecycle = {}): Promise<OpenSearchClient> {
  const client = new OpenSearchClient(source, lifecycle);
  try { await client.initialize(); }
  catch (error) { await client.dispose(); throw error; }
  lifecycle.onDispose?.(() => client.dispose());
  return client;
}
