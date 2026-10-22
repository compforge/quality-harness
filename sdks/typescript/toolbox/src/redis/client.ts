import type { Client } from "../client";
import type { ConnectionSource, ClientLifecycle } from "../datasource";
import { isConnectionNetworkError, type TcpTransport } from "../transport";
import { ToolboxError } from "../errors";
import { discoverRedisTopology, type RedisTopologyConfig, type RedisTopology } from "./topology";
import { createClient, SocketClosedUnexpectedlyError, ConnectionTimeoutError, type RedisClientType } from "@redis/client";

export interface RedisEndpoint {
  host: string;
  port: number;
}

export interface RedisMappedEndpoint extends RedisEndpoint {
  servername?: string;
}

export interface RedisCredentials {
  username?: string;
  password?: string;
}

export interface RedisConnectionConfig extends RedisCredentials {
  database: number;
  useSsl: boolean;
  timeoutMs: number;
  /** Per-route mapping and handshake budget; defaults to timeoutMs. */
  connectTimeoutMs?: number;
  /** Total acquisition budget; defaults to connectTimeoutMs times the number of routes. */
  acquireTimeoutMs?: number;
}

export type RedisEndpointMapper = (endpoint: RedisEndpoint) => Promise<RedisMappedEndpoint>;

type NodeRedisClient = RedisClientType;
type NodeRedisClientFactory = (options: Parameters<typeof createClient>[0]) => NodeRedisClient;

export interface RedisManagedConnection extends RedisConnectionApi {
  readonly isReady: boolean;
  close(): void;
}

export type RedisConnectionFactory = (
  endpoint: RedisEndpoint,
  mapped: RedisMappedEndpoint,
  config: RedisConnectionConfig & { signal?: AbortSignal; connectSignal?: AbortSignal },
) => Promise<RedisManagedConnection>;

function stringValue(value: unknown): string {
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  return String(value ?? "");
}

function scalar(value: string): string | number {
  if (/^-?(?:\d+|\d+\.\d+)$/.test(value)) return Number(value);
  return value;
}

/** Redis INFO wire 文本解析；协议适配留在 infra，领域层只接收结构化字段。 */
export function parseRedisInfo(raw: string): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf(":");
    if (separator < 1) continue;
    const name = line.slice(0, separator);
    const value = line.slice(separator + 1);
    if (/^db\d+$/.test(name)) {
      result[name] = Object.fromEntries(value.split(",").map((item) => {
        const index = item.indexOf("=");
        return [item.slice(0, index), scalar(item.slice(index + 1))];
      }));
    } else {
      result[name] = scalar(value);
    }
  }
  return result;
}

export interface RedisConnectionApi {
  readonly endpoint: RedisEndpoint;
  ping(): Promise<string>;
  info(section?: string): Promise<Record<string, unknown>>;
  dbSize(): Promise<number>;
  command(args: string[]): Promise<unknown>;
  pipeline(commands: string[][]): Promise<unknown[]>;
  scan(cursor: string, count: number): Promise<{ cursor: string; keys: string[] }>;
}

export interface RedisAccessApi {
  connection(
    endpoint: RedisEndpoint,
    database: number,
    credentials?: RedisCredentials,
  ): Promise<RedisConnectionApi>;
  close(): Promise<void>;
}

/** Await cancellation even when an injected transport cannot cancel its own preparation. */
async function abortable<T>(pending: Promise<T>, signal: AbortSignal, cancel?: () => void): Promise<T> {
  if (signal.aborted) {
    cancel?.();
    void pending.catch(() => {});
    signal.throwIfAborted();
  }
  let abort!: () => void;
  const cancelled = new Promise<never>((_, reject) => {
    abort = () => { cancel?.(); reject(signal.reason); };
    signal.addEventListener("abort", abort, { once: true });
  });
  try { return await Promise.race([pending, cancelled]); }
  finally { signal.removeEventListener("abort", abort); }
}

/** @spec A command is sent once; a later operation may acquire a new connection after failure. */
export class RedisConnection implements RedisManagedConnection {
  #abort?: () => void;
  private constructor(
    readonly endpoint: RedisEndpoint,
    private readonly client: NodeRedisClient,
    private readonly timeoutMs: number,
    private readonly lastError: () => Error | undefined,
    private readonly signal?: AbortSignal,
  ) {
    this.#abort = () => this.close();
    signal?.addEventListener("abort", this.#abort, { once: true });
  }

  static async connect(
    endpoint: RedisEndpoint,
    mapped: RedisMappedEndpoint,
    config: RedisConnectionConfig & { signal?: AbortSignal; connectSignal?: AbortSignal },
    clientFactory: NodeRedisClientFactory = createClient as NodeRedisClientFactory,
  ): Promise<RedisConnection> {
    config.signal?.throwIfAborted();
    config.connectSignal?.throwIfAborted();
    const baseSocket = {
      host: mapped.host, port: mapped.port,
      connectTimeout: config.connectTimeoutMs ?? config.timeoutMs,
      // Connection recovery belongs to RedisAccess; never queue or replay a command during reconnect.
      reconnectStrategy: false as const,
    };
    const socket = config.useSsl
      ? { ...baseSocket, tls: true as const, servername: mapped.servername ?? endpoint.host }
      : { ...baseSocket, tls: false as const };
    const client = clientFactory({ socket, database: config.database,
      username: config.username, password: config.password,
      disableOfflineQueue: true, commandOptions: { timeout: config.timeoutMs } });
    let lastError: Error | undefined;
    client.on("error", err => { lastError = err instanceof Error ? err : new Error(String(err)); });
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(new ToolboxError("Redis handshake timed out", { kind: "timeout", code: "ETIMEDOUT" })),
      config.connectTimeoutMs ?? config.timeoutMs);
    const signal = AbortSignal.any([timeout.signal, ...(config.signal ? [config.signal] : []),
      ...(config.connectSignal ? [config.connectSignal] : [])]);
    const close = () => { if (client.isOpen) client.destroy(); };
    try {
      await abortable(client.connect(), signal, close);
      signal.throwIfAborted();
      return new RedisConnection(endpoint, client, config.timeoutMs, () => lastError, config.signal);
    } catch (error) { close(); throw error; }
    finally { clearTimeout(timer); }
  }

  get isReady(): boolean { return !this.signal?.aborted && this.client.isReady; }

  async #execute<T>(operationName: string, operation: () => Promise<T>): Promise<T> {
    this.signal?.throwIfAborted();
    if (!this.client.isOpen) {
      const reason = this.lastError()?.message;
      throw new Error(`Redis connection is closed${reason ? `: ${reason}` : ""}`);
    }
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(new ToolboxError(
      `Redis ${operationName} timed out after ${this.timeoutMs}ms (${this.endpoint.host}:${this.endpoint.port})`,
      { kind: "timeout", code: "ETIMEDOUT" })), this.timeoutMs);
    const signal = this.signal ? AbortSignal.any([this.signal, timeout.signal]) : timeout.signal;
    try { return await abortable(Promise.resolve().then(() => { signal.throwIfAborted(); return operation(); }), signal, () => this.close()); }
    finally { clearTimeout(timer); }
  }

  ping(): Promise<string> {
    return this.#execute("PING", () => this.client.ping());
  }

  async info(section?: string): Promise<Record<string, unknown>> {
    const raw = await this.#execute(section ? `INFO ${section}` : "INFO", () => section
      ? this.client.sendCommand(["INFO", section])
      : this.client.sendCommand(["INFO"]));
    return parseRedisInfo(stringValue(raw));
  }

  async dbSize(): Promise<number> {
    return Number(await this.#execute("DBSIZE", () => this.client.sendCommand(["DBSIZE"])));
  }

  command(args: string[]): Promise<unknown> {
    return this.#execute(args[0]?.toUpperCase() ?? "command", () => this.client.sendCommand(args));
  }

  async pipeline(commands: string[][]): Promise<unknown[]> {
    const commandNames = new Set(commands.map((command) => command[0]?.toUpperCase()).filter(Boolean));
    const operationName = commandNames.size === 1 ? `${[...commandNames][0]} pipeline` : "pipeline";
    return await this.#execute(operationName, () => {
      const pipeline = this.client.multi();
      for (const command of commands) pipeline.addCommand(command);
      return pipeline.execAsPipeline();
    }) as unknown[];
  }

  async scan(cursor: string, count: number): Promise<{ cursor: string; keys: string[] }> {
    const reply = await this.command(["SCAN", cursor, "COUNT", String(count)]);
    if (!Array.isArray(reply) || !Array.isArray(reply[1])) throw new Error("Redis SCAN 返回格式无效");
    return { cursor: stringValue(reply[0]), keys: reply[1].map(stringValue) };
  }

  close(): void {
    if (this.#abort) this.signal?.removeEventListener("abort", this.#abort);
    if (this.client.isOpen) this.client.destroy();
  }
}

/**
 * @spec Only connection establishment can advance to another transport; protocol operations are never replayed.
 * @why Every topology node needs the same route policy, credentials, deadline and root cancellation.
 */
export class RedisAccess implements RedisAccessApi {
  readonly #connections = new Map<string, Promise<RedisManagedConnection>>();
  readonly #controller = new AbortController();
  readonly #signal: AbortSignal;
  readonly #transports: readonly TcpTransport[];
  #closing?: Promise<void>;

  constructor(
    transports: TcpTransport | readonly TcpTransport[],
    private readonly baseConfig: Omit<RedisConnectionConfig, "database">,
    private readonly connectionFactory: RedisConnectionFactory = RedisConnection.connect,
    private readonly lifecycle: ClientLifecycle = {},
  ) {
    this.#transports = Array.isArray(transports) ? transports : [transports as TcpTransport];
    if (!this.#transports.length) throw new Error("Redis requires a TCP transport");
    for (const value of [baseConfig.timeoutMs, baseConfig.connectTimeoutMs ?? baseConfig.timeoutMs,
      baseConfig.acquireTimeoutMs ?? (baseConfig.connectTimeoutMs ?? baseConfig.timeoutMs) * this.#transports.length]) {
      if (!Number.isFinite(value) || value <= 0) throw new Error("Redis timeouts must be positive milliseconds");
    }
    this.#signal = lifecycle.signal ? AbortSignal.any([lifecycle.signal, this.#controller.signal]) : this.#controller.signal;
  }

  async connection(endpoint: RedisEndpoint, database: number, credentials: RedisCredentials = this.baseConfig): Promise<RedisManagedConnection> {
    this.#signal.throwIfAborted();
    const key = [endpoint.host, endpoint.port, database, credentials.username ?? "", credentials.password ?? ""].join("\0");
    const existing = this.#connections.get(key);
    if (existing) {
      const connection = await existing;
      this.#signal.throwIfAborted();
      if (connection.isReady) return connection;
      if (this.#connections.get(key) === existing) {
        this.#connections.delete(key);
        connection.close();
      }
      return this.connection(endpoint, database, credentials);
    }
    const pending = this.#open(endpoint, database, credentials);
    this.#connections.set(key, pending);
    void pending.catch(() => { if (this.#connections.get(key) === pending) this.#connections.delete(key); });
    return pending;
  }

  async #open(endpoint: RedisEndpoint, database: number, credentials: RedisCredentials): Promise<RedisManagedConnection> {
    const timeout = new AbortController();
    const attemptBudget = this.baseConfig.connectTimeoutMs ?? this.baseConfig.timeoutMs;
    const budget = this.baseConfig.acquireTimeoutMs ?? attemptBudget * this.#transports.length;
    const deadline = performance.now() + budget;
    const timer = setTimeout(() => timeout.abort(new ToolboxError(
      `Redis connection timed out after ${budget}ms (${endpoint.host}:${endpoint.port})`,
      { kind: "timeout", code: "ETIMEDOUT" })), budget);
    const signal = AbortSignal.any([this.#signal, timeout.signal]);
    let reason: string | undefined;
    try {
      for (let index = 0; index < this.#transports.length; index++) {
        signal.throwIfAborted();
        const transport = this.#transports[index]!;
        const attempt = new AbortController();
        const remaining = Math.min(attemptBudget, Math.max(1, Math.ceil(deadline - performance.now())));
        const attemptTimer = setTimeout(() => attempt.abort(new ToolboxError(
          `Redis connection attempt timed out (${transport.name})`, { kind: "timeout", code: "ETIMEDOUT" })), remaining);
        const attemptSignal = AbortSignal.any([signal, attempt.signal]);
        try {
          const mapped = await abortable(transport.connect(endpoint), attemptSignal);
          attemptSignal.throwIfAborted();
          const pending = this.connectionFactory(endpoint, mapped, { ...this.baseConfig, ...credentials, database,
            connectTimeoutMs: remaining, signal: this.#signal, connectSignal: attemptSignal });
          // A factory that finishes after cancellation must not leak a live connection.
          const prepared = pending.then(connection => {
            if (attemptSignal.aborted) { connection.close(); attemptSignal.throwIfAborted(); }
            return connection;
          });
          const connection = await abortable(prepared, attemptSignal);
          this.lifecycle.onRoute?.({ transport: mapped.host === endpoint.host && mapped.port === endpoint.port ? "direct" : transport.name, reason });
          return connection;
        } catch (failure) {
          signal.throwIfAborted();
          const error = attemptSignal.aborted ? attemptSignal.reason : failure;
          const network = isConnectionNetworkError(error) || error instanceof SocketClosedUnexpectedlyError || error instanceof ConnectionTimeoutError;
          if (!network || index === this.#transports.length - 1) throw error;
          reason = error instanceof SocketClosedUnexpectedlyError ? "connection_lost"
            : error instanceof ConnectionTimeoutError ? "ETIMEDOUT" : String((error as NodeJS.ErrnoException).code);
        } finally { clearTimeout(attemptTimer); }
      }
      throw new Error("Redis requires a TCP transport");
    } finally { clearTimeout(timer); }
  }

  close(): Promise<void> {
    return this.#closing ??= (async () => {
      this.#controller.abort(new Error("Redis access is closed"));
      const settled = await Promise.allSettled(this.#connections.values());
      this.#connections.clear();
      const errors: unknown[] = [];
      for (const result of settled) if (result.status === "fulfilled") {
        try { result.value.close(); } catch (error) { errors.push(error); }
      }
      if (errors.length) throw new AggregateError(errors, "Redis connection cleanup failed");
    })();
  }
}

/** Connection configuration includes topology; diagnostics do not need a second target model. */
export type RedisDataSourceTarget = RedisConnectionConfig & RedisTopologyConfig;

export class RedisClient implements Client {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  #initialization?: Promise<void>;
  #disposal?: Promise<void>;
  #access?: RedisAccess;
  #target?: RedisDataSourceTarget;
  constructor(private readonly source: ConnectionSource<RedisDataSourceTarget>, private readonly lifecycle: ClientLifecycle = {}) {
    this.signal = lifecycle.signal ? AbortSignal.any([lifecycle.signal, this.#controller.signal]) : this.#controller.signal;
  }
  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= (async () => {
      this.#target = await this.source.resolve();
      this.signal.throwIfAborted();
      if (!this.source.transports.length || this.source.transports.some(transport => transport.kind !== "tcp")) {
        throw new Error("Redis requires TCP transports");
      }
      if (this.#target.clusterType === "cluster" && this.#target.database !== 0) throw new Error("Redis Cluster requires database 0");
      this.#access = new RedisAccess(this.source.transports as readonly TcpTransport[], this.#target,
        undefined, { ...this.lifecycle, signal: this.signal });
    })();
  }
  get access(): RedisAccess {
    this.signal.throwIfAborted();
    if (!this.#access) throw new Error("Redis client is not initialized");
    return this.#access;
  }
  /** Refresh topology for this observation, while retaining reusable node connections. */
  topology(): Promise<RedisTopology> { return discoverRedisTopology(this.access, this.target); }

  get target(): RedisDataSourceTarget {
    if (!this.#target) throw new Error("Redis client is not initialized");
    return this.#target;
  }
  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("Redis client disposed"));
      await this.#initialization?.catch(() => {});
      await this.#access?.close();
    })();
  }
}

export async function openRedis(source: ConnectionSource<RedisDataSourceTarget>, lifecycle: ClientLifecycle = {}): Promise<RedisClient> {
  const client = new RedisClient(source, lifecycle);
  try { await client.initialize(); }
  catch (error) { await client.dispose(); throw error; }
  lifecycle.onDispose?.(() => client.dispose());
  return client;
}
