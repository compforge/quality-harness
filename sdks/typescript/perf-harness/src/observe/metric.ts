import type { PrometheusObservation, ScalarSummary, HistogramQuantile } from "./model";

export function seriesId(metric: string, labels: Record<string, string>): string {
  const entries = Object.entries(labels).sort(([a], [b]) => a.localeCompare(b));
  return metric + (entries.length ? `{${entries.map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(",")}}` : "");
}
export function finiteNumber(value: unknown): number {
  if ((typeof value !== "string" && typeof value !== "number") || String(value).trim() === "")
    throw new Error("Prometheus query returned an invalid numeric sample");
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error("Prometheus query returned a non-finite sample");
  return result;
}
function sample(row: unknown): {labels: Record<string, string>; value: number} {
  if (!row || typeof row !== "object") throw new Error("Invalid Prometheus vector row");
  const item = row as {metric?: Record<string, string>; value?: unknown[]};
  if (!item.metric || typeof item.metric !== "object" || Array.isArray(item.metric) || !Array.isArray(item.value) || item.value.length !== 2)
    throw new Error("Invalid Prometheus vector sample");
  const labels = Object.fromEntries(Object.entries(item.metric).filter(([key]) => key !== "__name__"));
  if (Object.values(labels).some(value => typeof value !== "string")) throw new Error("Invalid Prometheus labels");
  return {labels, value: finiteNumber(item.value[1])};
}
/** @spec Histogram overflow is +Inf; arbitrary non-finite scalar samples are failures. */
function histogram(rows: unknown[], projection: HistogramQuantile): ScalarSummary | undefined {
  if (!(projection.quantile > 0 && projection.quantile < 1) || !Number.isFinite(projection.scale) || projection.scale <= 0)
    throw new Error("Invalid histogram projection");
  const buckets = new Map<number, number>();
  for (const row of rows) {
    const {labels, value} = sample(row);
    if (Object.keys(labels).join() !== "le") throw new Error("histogram query must aggregate by le only");
    const bound = labels.le === "+Inf" ? Infinity : finiteNumber(labels.le);
    if (Number.isNaN(bound) || bound < 0 || value < 0 || buckets.has(bound)) throw new Error("histogram has invalid or duplicate buckets");
    buckets.set(bound, value);
  }
  const bounds = [...buckets.keys()].sort((a,b) => a-b);
  if (bounds.length < 2 || bounds.at(-1) !== Infinity) throw new Error("histogram requires finite buckets and a +Inf bucket");
  let before = 0;
  for (const bound of bounds) {
    if (buckets.get(bound)! < before) throw new Error("histogram bucket counts are not monotonic");
    before = buckets.get(bound)!;
  }
  const total = before;
  if (!total) return undefined;
  const rank = projection.quantile * total;
  let lower = 0, value: number | "+Inf" = "+Inf";
  before = 0;
  for (const bound of bounds) {
    const count = buckets.get(bound)!;
    if (count >= rank) {
      if (Number.isFinite(bound)) value = (lower + (bound-lower)*(rank-before)/(count-before))*projection.scale;
      break;
    }
    lower = bound; before = count;
  }
  return {kind: "scalar", value, caveats: [], histogram: {quantile: projection.quantile,
    sample_count: total, max_finite_bucket: bounds.at(-2)!*projection.scale, overflow: value === "+Inf"}};
}
export function projectObservation(query: PrometheusObservation, fraction: boolean): ScalarSummary | undefined {
  if (!query.data) throw new Error("Prometheus query has no result");
  if (query.warnings.length) throw new Error("Prometheus query returned warnings; observation may be incomplete");
  const {resultType, result} = query.data;
  if (!Array.isArray(result)) throw new Error("Invalid Prometheus result");
  if (query.histogram) {
    if (resultType !== "vector") throw new Error("histogram query must return a vector grouped only by le");
    if (!result.length) return undefined;
    return histogram(result, query.histogram);
  }
  let value: number;
  if (resultType === "scalar") {
    if (result.length !== 2) throw new Error("Invalid Prometheus scalar");
    value = finiteNumber(result[1]);
  } else if (resultType === "vector") {
    if (!result.length) return undefined;
    if (result.length !== 1) throw new Error("Operation summary must return exactly one series");
    const projected = sample(result[0]);
    if (Object.keys(projected.labels).length) throw new Error("Operation summary returned undeclared labels");
    value = projected.value;
  } else throw new Error("Prometheus instant query must return scalar or vector");
  if (fraction && !(0 <= value && value <= 1)) throw new Error("ratio observation must be between zero and one");
  return {kind: "scalar", value, caveats: []};
}

/** Project each evaluation timestamp independently, including bucket vectors for histogram quantiles. */
export function projectSeries(query: PrometheusObservation, origin: number, fraction: boolean): [number, number][] {
  if (query.warnings.length || query.data?.resultType !== "matrix" || !Array.isArray(query.data.result))
    throw new Error("Expected a complete MetricDB range matrix");
  const ticks = new Map<number, {metric: Record<string,string>; value: [number,string]}[]>();
  for (const row of query.data.result) {
    if (!row.metric || !Array.isArray(row.values)) throw new Error("Invalid matrix series");
    for (const sample of row.values) {
      if (!Array.isArray(sample) || sample.length !== 2 || !Number.isFinite(sample[0])) throw new Error("Invalid range sample");
      const rows = ticks.get(sample[0]) ?? [];
      rows.push({metric:row.metric,value:[sample[0],sample[1]]});
      ticks.set(sample[0],rows);
    }
  }
  const points: [number,number][] = [];
  for (const [t,rows] of [...ticks].sort(([a],[b]) => a-b)) {
    if (rows.some(row => ["NaN","+Inf","-Inf"].includes(row.value[1]))) continue;
    const summary = projectObservation({...query,data:{resultType:"vector",result:rows}},fraction);
    if (typeof summary?.value === "number") points.push([t-origin,summary.value]);
  }
  return points;
}
