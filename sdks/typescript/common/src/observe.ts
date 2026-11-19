/** Metadata for a measured quantity. */
export interface MetricDefinition {
  /** Semantic series category. Unrecognized or absent categories are custom metrics. */
  readonly category?: string;
  readonly unit: string;
  readonly description?: string;
  readonly semantics?: string;
}
export interface HistogramQuantile { quantile: number; scale: number; }
export interface MetricQuery extends MetricDefinition {
  readonly expression: string;
  /** Rolling lookback for $window in an Operation time series, in seconds. */
  readonly trendWindowSeconds?: number;
  readonly histogram?: HistogramQuantile;
}
/**
 * The containing Service, Operation or Workload supplies the observation target.
 * @spec type selects the observation capability; kind restricts its concrete backend.
 * An omitted kind applies to every backend; it is not a fallback for a failing query.
 * The host owns source selection; declarations contain no endpoint or credentials.
 */
export interface ObservationDefinition {
  readonly type: string;
  /** Open backend name, e.g. prometheus or victoriametrics; the client owns supported kinds. */
  readonly kind?: string;
  /** Omitted inherits Operation; null declares metrics shared across response modes. */
  readonly responseMode?: "unary" | "sse" | null;
  readonly metrics: Readonly<Record<string, MetricQuery>>;
}
/** @spec Every declared role has exactly one matching producer; selection never implements fallback. */
export function selectObservations(observations: readonly ObservationDefinition[], backend: string): readonly ObservationDefinition[] {
  for (const observation of observations) {
    if (!observation.type?.trim() || !Object.keys(observation.metrics).length) throw new Error("Observation requires a type and metric roles");
    if (observation.kind !== undefined && (typeof observation.kind !== "string" || !observation.kind.trim())) throw new Error("Observation kind must be a nonempty string");
    for (const [role, metric] of Object.entries(observation.metrics)) {
      if (!role.trim() || !metric.unit?.trim() || !metric.expression?.trim()) throw new Error("Metric requires role, unit and expression");
    }
  }
  const selected = observations.filter(observation => observation.kind === undefined || observation.kind === backend);
  const roles = new Set(observations.flatMap(observation => Object.keys(observation.metrics)));
  for (const role of roles) {
    const producers = selected.filter(observation => Object.hasOwn(observation.metrics, role));
    if (producers.length !== 1) throw new Error(`Metric '${role}' requires exactly one producer for backend '${backend}'`);
  }
  return selected;
}
