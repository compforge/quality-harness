export interface DatabaseIdentity {
  user: string;
  password: string;
}

export interface DatabaseTarget extends DatabaseIdentity {
  host: string;
  port: number;
  database: string;
}

export type DatabaseRow = Record<string, unknown>;

export interface SqlStatement {
  sql: string;
  values: readonly unknown[];
}

export interface DatabaseQueryLimits {
  timeoutMs: number;
  maxRows: number;
  /** Maximum retained UTF-8 JSON row bytes, not a limit on an individual wire packet. */
  maxBytes: number;
}

export interface DatabaseQueryResult {
  rows: DatabaseRow[];
  columns: string[];
  bytes: number;
  truncated: boolean;
  truncation?: "rows" | "bytes";
}

/** Storage-neutral operations shared by infrastructure consumers. */
export interface Database {
  query(
    target: DatabaseTarget,
    sql: string,
    values: readonly unknown[],
  ): Promise<DatabaseRow[]>;
  queryOne(
    target: DatabaseTarget,
    sql: string,
    values: readonly unknown[],
  ): Promise<DatabaseRow | undefined>;
  /** Independent statements in submission order; fail-fast, without atomicity or replay. */
  queryBatch?(target: DatabaseTarget, statements: readonly SqlStatement[]): Promise<DatabaseRow[][]>;
  close(): Promise<void>;
}
