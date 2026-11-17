export type MetricDBKind = "prometheus" | "victoriametrics";
export interface MetricDBResult {
  data: { resultType: string; result: unknown };
  warnings: string[];
  infos: string[];
  /** Absent means the backend supplied no completeness flag. */
  isPartial?: boolean;
}
/** A borrowed query channel. The host owns HTTP pooling, credentials, TLS and disposal. */
export interface MetricDBReader {
  readonly url: string;
  readonly kind: MetricDBKind;
  query(expression: string, time: number, signal?: AbortSignal): Promise<MetricDBResult>;
  queryRange(expression: string, start: number, end: number, step: number, signal?: AbortSignal): Promise<MetricDBResult>;
}

/** @spec Backend adapters own wire differences, never expressions, RED semantics or HTTP pools. */
export interface MetricDBBackend {
  readonly kind: MetricDBKind;
  queryParameters(): Readonly<Record<string, string>>;
}
