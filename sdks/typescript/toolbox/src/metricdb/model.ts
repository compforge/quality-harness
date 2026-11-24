import type { ScrapeConfig, ScrapeTarget } from "@compforge/harness-common";

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

/** Read from the collector that owns discovery and scraping. */
export interface ScrapeReader {
  getScrapeConfig(signal?: AbortSignal, onYaml?: (yaml: string) => void): Promise<ScrapeConfig[]>;
  listScrapeTargets(options?: { state?: "active" | "dropped" | "any" }, signal?: AbortSignal, onBody?: (body: Uint8Array) => void): Promise<ScrapeTarget[]>;
}

/** Kubernetes identity and address shared by discovered scrape collectors. */
export interface CollectorEndpoint {
  namespace: string;
  service: string;
  url: string;
}

/** @spec Backend adapters own wire differences, never expressions, RED semantics or HTTP pools. */
export interface MetricDBBackend {
  readonly kind: MetricDBKind;
  queryParameters(): Readonly<Record<string, string>>;
}
