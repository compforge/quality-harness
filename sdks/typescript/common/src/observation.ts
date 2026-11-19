export interface MetricBinding { metric: string; unit: string; description?: string; semantics?: string; }
export interface ObservationBinding {
  target: string; service: string; metrics: Record<string, MetricBinding>;
  workload?: string | null; method?: string; path?: string; response_mode?: string | null;
}
