import type { Client } from "../client";
import { ConcurrencyPool } from "../concurrency";
import { createConnection, type Connection, type ConnectionOptions, type RowDataPacket } from "mysql2/promise";
import type { Database, DatabaseTarget, DatabaseRow, DatabaseQueryLimits, DatabaseQueryResult, SqlStatement } from "./types";
import { destroyMysqlConnection } from "./connection";
import { queryReadonlySession, validateQueryLimits } from "./readonly";
import type { ConnectionSource, ClientLifecycle } from "../datasource";
import { isConnectionNetworkError, type Transport, type PodPythonTransport } from "../transport";
import { queryMysqlViaPod, queryMysqlViaPodBatch } from "./pod";

export interface MysqlDatabaseOptions extends ClientLifecycle { connectTimeoutMs: number; queryTimeoutMs: number }
type ConnectionFactory = (options: ConnectionOptions) => Promise<Connection>;
type Session = { kind: "tcp"; connection: Connection } | { kind: "python"; transport: PodPythonTransport };

/** SQL is executed once; only native connection establishment may advance to another transport. */
export class MysqlDatabase implements Database {
  // A client owns one native session per target; serialize queries on both transports.
  readonly #queries = new ConcurrencyPool(1);
  readonly #connections = new Map<string, Promise<Session>>();
  constructor(
    private readonly transports: readonly Transport[],
    private readonly options: MysqlDatabaseOptions,
    private readonly connect: ConnectionFactory = createConnection,
  ) {}

  query(target: DatabaseTarget, sql: string, values: readonly unknown[]): Promise<DatabaseRow[]> {
    return this.#queries.run(() => this.#query(target, sql, values), this.options.signal);
  }

  /**
   * Independent statements in submission order, one session: a Pod transport pays the exec
   * and connection cost once instead of per statement. Fail-fast like query(); use query()
   * when a statement depends on an earlier result.
   */
  queryBatch(target: DatabaseTarget, statements: readonly SqlStatement[]): Promise<DatabaseRow[][]> {
    return this.#queries.run(async () => {
      if (!statements.length) return [];
      return this.#withSession(target, async (session) => {
        if (session.kind === "python") return queryMysqlViaPodBatch(session.transport, target, statements, this.options);
        const results: DatabaseRow[][] = [];
        for (const { sql, values } of statements) {
          const [rows] = await session.connection.execute<RowDataPacket[]>({ sql, values: [...values], timeout: this.options.queryTimeoutMs });
          results.push(rows as DatabaseRow[]);
        }
        return results;
      });
    }, this.options.signal);
  }

  /** Bounded, single-statement prepared execution on an isolated READ ONLY session (native only). */
  queryReadonly(target: DatabaseTarget, sql: string, values: readonly unknown[], limits: DatabaseQueryLimits): Promise<DatabaseQueryResult> {
    validateQueryLimits(limits);
    return this.#queries.run(async () => {
      const session = await this.#open(target);
      if (session.kind !== "tcp") throw new Error("Bounded readonly MySQL queries require a native TCP transport");
      try {
        return await queryReadonlySession(session.connection, sql, values, limits, this.options.signal);
      } finally { destroyMysqlConnection(session.connection); }
    }, this.options.signal);
  }

  initialize(target: DatabaseTarget): Promise<void> {
    return this.#queries.run(async () => { await this.#session(target); }, this.options.signal);
  }

  #session(target: DatabaseTarget): Promise<Session> {
    this.options.signal?.throwIfAborted();
    const key = [target.host, target.port, target.database, target.user, target.password].join("\0");
    let pending = this.#connections.get(key);
    if (!pending) {
      pending = this.#open(target);
      this.#connections.set(key, pending);
      void pending.catch(() => { if (this.#connections.get(key) === pending) this.#connections.delete(key); });
    }
    return pending;
  }

  async #withSession<T>(target: DatabaseTarget, run: (session: Session) => Promise<T>): Promise<T> {
    this.options.signal?.throwIfAborted();
    const key = [target.host, target.port, target.database, target.user, target.password].join("\0");
    const pending = this.#session(target);
    let session: Session | undefined;
    try {
      session = await pending;
      this.options.signal?.throwIfAborted();
      return await run(session);
    } catch (error) {
      if (this.#connections.get(key) === pending) this.#connections.delete(key);
      if (session?.kind === "tcp") destroyMysqlConnection(session.connection);
      throw error;
    }
  }

  async #query(target: DatabaseTarget, sql: string, values: readonly unknown[]): Promise<DatabaseRow[]> {
    return this.#withSession(target, async (session) => {
      if (session.kind === "python") return queryMysqlViaPod(session.transport, target, sql, values, this.options);
      const [rows] = await session.connection.execute<RowDataPacket[]>({ sql, values: [...values], timeout: this.options.queryTimeoutMs });
      return rows as DatabaseRow[];
    });
  }

  async #open(target: DatabaseTarget): Promise<Session> {
    let reason: string | undefined;
    for (let i = 0; i < this.transports.length; i++) {
      const transport = this.transports[i]!;
      this.options.signal?.throwIfAborted();
      if (transport.kind === "python") {
        this.options.onRoute?.({ transport: transport.name, reason });
        return { kind: "python", transport };
      }
      try {
        const endpoint = await transport.connect(target);
        const connection = await this.connect({
          host: endpoint.host, port: endpoint.port, user: target.user, password: target.password, database: target.database,
          connectTimeout: this.options.connectTimeoutMs, dateStrings: true, supportBigNumbers: true, bigNumberStrings: true,
        });
        this.options.onRoute?.({ transport: endpoint.host === target.host && endpoint.port === target.port ? "direct" : transport.name, reason });
        return { kind: "tcp", connection };
      } catch (error) {
        if (!isConnectionNetworkError(error) || i === this.transports.length - 1) throw error;
        reason = String((error as NodeJS.ErrnoException).code);
      }
    }
    throw new Error("MySQL DataSource has no transport");
  }

  async queryOne(target: DatabaseTarget, sql: string, values: readonly unknown[]): Promise<DatabaseRow | undefined> {
    return (await this.query(target, sql, values))[0];
  }
  close(): Promise<void> {
    return this.#queries.run(async () => {
      const pending = [...this.#connections.values()];
      this.#connections.clear();
      for (const result of await Promise.allSettled(pending)) {
        if (result.status === "fulfilled" && result.value.kind === "tcp") destroyMysqlConnection(result.value.connection);
      }
    });
  }
}

/** A datasource-bound MySQL client; the protocol adapter still supports explicit multi-target diagnostics. */
export class MysqlClient<Target extends DatabaseTarget = DatabaseTarget> implements Client {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  #initialization?: Promise<void>;
  #disposal?: Promise<void>;
  #database?: MysqlDatabase;
  #target?: Target;

  constructor(private readonly source: ConnectionSource<Target>, private readonly options: MysqlDatabaseOptions) {
    this.signal = options.signal ? AbortSignal.any([options.signal, this.#controller.signal]) : this.#controller.signal;
  }

  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= (async () => {
      this.#target = await this.source.resolve();
      this.signal.throwIfAborted();
      this.#database = new MysqlDatabase(this.source.transports, { ...this.options, signal: this.signal });
      await this.#database.initialize(this.#target);
    })();
  }

  get database(): MysqlDatabase {
    if (!this.#database) throw new Error("MySQL client is not initialized");
    return this.#database;
  }
  get target(): Target {
    if (!this.#target) throw new Error("MySQL client is not initialized");
    return this.#target;
  }

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("MySQL client disposed"));
      await this.#initialization?.catch(() => {});
      await this.#database?.close();
    })();
  }
}

export async function openMysql<Target extends DatabaseTarget>(source: ConnectionSource<Target>, options: MysqlDatabaseOptions): Promise<MysqlClient<Target>> {
  const client = new MysqlClient(source, options);
  try { await client.initialize(); }
  catch (error) { await client.dispose(); throw error; }
  options.onDispose?.(() => client.dispose());
  return client;
}
