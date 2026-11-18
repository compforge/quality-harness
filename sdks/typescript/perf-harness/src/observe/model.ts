import type { MetricDBResult, MetricDBKind } from "@compforge/harness-toolbox/metricdb";
import type { WindowKind, Phase } from "../model";

export const RED_UNITS = {qps: "rps", p95: "s", error_rate: "ratio"} as const;
export type { MetricBinding, ObservationBinding, HistogramQuantile } from "@compforge/harness-common";
import type { HistogramQuantile } from "@compforge/harness-common";
export interface ScalarSummary {
  kind: "scalar"; value: number | "+Inf"; caveats: string[];
  histogram?: {quantile: number; sample_count: number; max_finite_bucket: number; overflow: boolean} | null;
}
/** Canonical replayable query facts; authentication is never persisted. */
export interface PrometheusObservation {
  probe: string; metric: string; expression: string; source_url: string; datasource_kind: MetricDBKind;
  start_s: number; end_s: number; step_s: number | null;
  labels: Record<string, string>; result_labels: string[]; window_id: string | null;
  data: MetricDBResult["data"] | null; warnings: string[]; infos: string[];
  error: string | null; collected_at_s: number | null; histogram: HistogramQuantile | null;
}
export interface WindowSelector { kind: WindowKind; phase?: Phase; name?: string; level?: number; }
export interface WindowQuery {
  name: string; promql: string; unit: string; category?: string; description?: string;
  trendWindowSeconds?: number;
  window?: WindowSelector; histogram?: HistogramQuantile;
}
export interface PrometheusProbe {
  scope?: "operation" | "service";
  service: string; target: string; method?: string; path?: string; response_mode?: string | null;
  summaries: readonly WindowQuery[];
  metrics: Readonly<Record<string, string>>;
  qps_semantics?: "entered" | "completed";
}
