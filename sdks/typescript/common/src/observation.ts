/** A reusable observation declaration, independent of execution phases and access credentials. */
export interface MetricDefinition {
  readonly unit: string;
  readonly description?: string;
  readonly semantics?: string;
}
export interface HistogramQuantile { quantile: number; scale: number; }
export interface MetricQuery extends MetricDefinition {
  readonly expression: string;
  readonly histogram?: HistogramQuantile;
}
/** The adapter type owns expression syntax; the host binds parameters and selects the backend. */
export interface ProbeDefinition {
  readonly type: string;
  readonly datasourceKind?: string;
  /** Omitted inherits Operation; null declares metrics shared across response modes. */
  readonly responseMode?: "unary" | "sse" | null;
  readonly metrics: Readonly<Record<string, MetricQuery>>;
}
export interface MetricBinding { metric: string; unit: string; description?: string; semantics?: string; }
export interface ObservationBinding {
  target: string; service: string; metrics: Record<string, MetricBinding>;
  workload?: string | null; method?: string; path?: string; response_mode?: string | null;
}

/** @spec Every declared role has exactly one matching producer; selection never implements fallback. */
export function selectProbes(probes: readonly ProbeDefinition[], backend: string): readonly ProbeDefinition[] {
  for (const probe of probes) {
    if (!probe.type?.trim() || !Object.keys(probe.metrics).length) throw new Error("Probe requires a type and metric roles");
    for (const [role, metric] of Object.entries(probe.metrics)) {
      if (!role.trim() || !metric.unit?.trim() || !metric.expression?.trim()) throw new Error("Metric requires role, unit and expression");
    }
  }
  const selected = probes.filter(probe => probe.datasourceKind === undefined || probe.datasourceKind === backend);
  const roles = new Set(probes.flatMap(probe => Object.keys(probe.metrics)));
  for (const role of roles) {
    const producers = selected.filter(probe => Object.hasOwn(probe.metrics, role));
    if (producers.length !== 1) throw new Error(`Metric '${role}' requires exactly one producer for backend '${backend}'`);
  }
  return selected;
}
