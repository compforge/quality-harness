import type { PrometheusResult, PrometheusKind } from "@compforge/harness-toolbox/prometheus";
import type { WindowKind, Phase } from "../model";

export const RED_UNITS = {qps: "rps", p95: "s", error_rate: "ratio"} as const;
export interface MetricBinding { metric: string; unit: string; description?: string; semantics?: string; }
export interface ObservationBinding {
  target: string; service: string; metrics: Record<string, MetricBinding>;
  workload?: string | null; method?: string; path?: string; response_mode?: string | null;
}
export interface HistogramQuantile { quantile: number; scale: number; }
export interface ScalarSummary {
  kind: "scalar"; value: number | "+Inf"; caveats: string[];
  histogram?: {quantile: number; sample_count: number; max_finite_bucket: number; overflow: boolean} | null;
}
/** Canonical replayable query facts; authentication is never persisted. */
export interface PrometheusObservation {
  probe: string; metric: string; expression: string; source_url: string; datasource_kind: PrometheusKind;
  start_s: number; end_s: number; step_s: number | null;
  labels: Record<string, string>; result_labels: string[]; window_id: string | null;
  data: PrometheusResult["data"] | null; warnings: string[]; infos: string[];
  error: string | null; collected_at_s: number | null; histogram: HistogramQuantile | null;
}
export interface WindowSelector { kind: WindowKind; phase?: Phase; name?: string; level?: number; }
export interface WindowQuery {
  name: string; promql: string; unit: string; description?: string;
  window?: WindowSelector; histogram?: HistogramQuantile;
}
export interface PrometheusProbe {
  service: string; target: string; method?: string; path?: string; response_mode?: string;
  summaries: readonly WindowQuery[];
  metrics: Readonly<Record<string, string>>;
  qps_semantics?: "entered" | "completed";
}
