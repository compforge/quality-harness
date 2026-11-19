import { workloadObservationTarget } from "./operations";
import type { MetricDBReader } from "@compforge/harness-toolbox/metricdb";
import { Graph, RelationKind, serviceId } from "@compforge/harness-common";
import type { Run, Window } from "../model";
import { RED_UNITS, type PrometheusProbe, type ObservationBinding, type PrometheusObservation } from "./model";
import { projectObservation, projectSeries, seriesId } from "./metric";

export function matches(window: Window, query: Pick<PrometheusProbe["summaries"][number], "window">): boolean {
  const select = query.window ?? {kind: "full"};
  return window.kind === select.kind && (select.phase == null || select.phase === window.phase)
    && (select.name == null || select.name === window.name)
    && (select.level == null || select.level === window.target_level);
}

/**
 * @spec Freeze expressions and half-open execution windows before any external I/O.
 * @rule Backend-specific PromQL belongs to the project; never rewrite it when recollecting.
 * @see {@link component://sdks/typescript/perf-harness/docs/observations.md}
 */
export function planMetrics(run: Run, probes: readonly PrometheusProbe[], source: Pick<MetricDBReader, "url" | "kind">): void {
  const url = new URL(source.url);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error("Metric source URL must not contain credentials, query or fragment");
  if (!["prometheus", "victoriametrics"].includes(source.kind)) throw new Error("Unsupported metric backend");
  if (run.executions.some(arm => arm.prometheus_observations?.length)) throw new Error("Run already has frozen metric queries; recollect instead of replanning");
  const graph = run.graph ? Graph.fromJSON(run.graph) : undefined;
  if (graph && graph.environment !== run.environment) throw new Error("Graph environment must match Run");

  const bindings: ObservationBinding[] = probes.flatMap(probe => {
    if (!probe.target || !probe.service) throw new Error("Metric probes require a target and a service");
    const serviceScope = probe.scope === "service" || probe.scope === "workload";
    if (serviceScope) {
      const owner = serviceId(probe.service);
      const target = probe.scope === "workload" ? workloadObservationTarget(probe.service, probe.workload ?? "") : owner;
      if (probe.target !== target || (graph && graph.node(owner)?.kind !== "service"))
        throw new Error(`Unknown observation target: ${probe.target}`);
    } else if (graph && (!graph.node(probe.target) || !graph.relationsFrom(probe.target, RelationKind.BelongsTo)
      .some(relation => decodeURIComponent(relation.target.replace(/^service:/,"")) === probe.service)))
      throw new Error(`Metric target or owner missing from graph: ${probe.target}`);
    if (probe.qps_semantics && !["entered", "completed"].includes(probe.qps_semantics)) throw new Error("Invalid QPS semantics");
    for (const [index, query] of probe.summaries.entries()) {
      if (!query.name || !query.promql.trim() || !query.unit) throw new Error("Summary requires name, expression and unit");
      const selector = query.window ?? {kind: "full"};
      if (!["full", "phase", "custom"].includes(selector.kind) || (selector.phase !== undefined &&
        (selector.kind !== "phase" || !["warmup", "hold", "cooldown"].includes(selector.phase)))) throw new Error("Invalid observation window selector");
      for (const previous of probe.summaries.slice(0,index).filter(item => item.name === query.name)) {
        if (previous.unit !== query.unit || previous.category !== query.category || previous.description !== query.description)
          throw new Error("Metric descriptions and units must agree across windows");
        if (run.executions.some(arm => arm.windows.some(window => matches(window, previous) && matches(window, query))))
          throw new Error("Duplicate summary in an observation window");
      }
      if (query.trendWindowSeconds !== undefined && (!Number.isSafeInteger(query.trendWindowSeconds) || query.trendWindowSeconds <= 0))
        throw new Error("Trend lookback must be a positive integer in seconds");
      if (query.histogram && (!(query.histogram.quantile > 0 && query.histogram.quantile < 1) ||
        !Number.isFinite(query.histogram.scale) || query.histogram.scale <= 0)) throw new Error("Invalid histogram projection");
    }
    if (serviceScope) return [];
    if (!Object.keys(probe.metrics).length) throw new Error("Probe requires metric bindings");
    const labels = {service: probe.service, operation: probe.target};
    const metrics = Object.fromEntries(Object.entries(probe.metrics).map(([role, name]) => {
      const summary = probe.summaries.find(query => query.name === name);
      if (!role || !summary) throw new Error(`Metric binding references unknown summary: ${name}`);
      if (Object.hasOwn(RED_UNITS,role) && summary.unit !== RED_UNITS[role as keyof typeof RED_UNITS]) throw new Error(`Invalid unit for ${role}`);
      return [role, {metric: `${seriesId(`prometheus_query.${name}`, labels)}.value`, unit: summary.unit,
        description: summary.description ?? "", semantics: role === "qps" ? probe.qps_semantics ?? "completed" : ""}];
    }));
    return [{target: probe.target, service: probe.service, metrics, method: probe.method ?? "", path: probe.path ?? "",
      response_mode: probe.response_mode ?? null, workload: null}];
  });
  const merged = new Map<string,ObservationBinding>();
  for (const binding of bindings) {
    const previous = merged.get(binding.target);
    if (!previous) { merged.set(binding.target,binding); continue; }
    for (const key of ["service","method","path","response_mode"] as const)
      if (previous[key] !== binding[key]) throw new Error("Metric producers disagree on operation identity");
    if (Object.keys(binding.metrics).some(role => Object.hasOwn(previous.metrics,role)))
      throw new Error("Metric producers must contribute unique roles for a target");
    previous.metrics = {...previous.metrics,...binding.metrics};
  }
  const descriptors = new Map<string,{unit:string; description:string; category?:string}>();
  for (const probe of probes) for (const summary of probe.summaries) {
    const description = summary.description ?? "", existing = descriptors.get(summary.name);
    if (existing && (existing.unit !== summary.unit || existing.category !== summary.category || existing.description !== description))
      throw new Error("Metric family units and descriptions must agree across producers");
    descriptors.set(summary.name,{unit:summary.unit,description,category:summary.category});
  }
  const plans = run.executions.map(arm => {
    if (arm.windows.length && !Number.isFinite(arm.started_at_s)) throw new Error(`Arm ${arm.id} has no recorded Unix observation origin`);
    const queries: PrometheusObservation[] = [];
    for (const probe of probes) for (const window of arm.windows) {
      if (![window.start_s,window.end_s].every(Number.isFinite) || window.end_s < window.start_s)
        throw new Error(`Invalid observation window: ${window.id}`);
      // Match Python's millisecond-resolution [start,end) query boundary.
      const start = Math.ceil((arm.started_at_s! + window.start_s)*1000)/1000;
      const end = (Math.ceil((arm.started_at_s! + window.end_s)*1000)-1)/1000;
      if (end < start) continue;
      for (const query of probe.summaries.filter(item => matches(window,item))) {
        const labels: Record<string,string> = {service: probe.service};
        if (probe.scope === "workload") labels.workload = probe.workload!;
        else if (probe.scope !== "service") labels.operation = probe.target;
        const instant: PrometheusObservation = {probe: `prometheus_query.${probe.service}.${probe.target}`, metric: `prometheus_query.${query.name}`,
          expression: query.promql.replaceAll("$window", `${Math.round((end-start)*1000)+1}ms`),
          source_url: source.url, datasource_kind: source.kind, start_s: start, end_s: end, step_s: null,
          labels, result_labels: [], window_id: window.id,
          data: null, warnings: [], infos: [], error: null, collected_at_s: null, histogram: query.histogram ?? null};
        if (labels.operation) queries.push(instant);
        if (window.kind === "full") {
          const step = Math.max(15, Math.ceil((end-start)/1000));
          queries.push({...instant, window_id: null, step_s: step,
            expression: query.promql.replaceAll("$window", `${query.trendWindowSeconds ?? 60}s`)});
        }
      }
    }
    const addresses = queries.map(query => `${query.window_id}/${seriesId(query.metric,query.labels)}`);
    if (new Set(addresses).size !== addresses.length) throw new Error("Duplicate metric query for a target/window");
    return queries;
  });
  run.observation_bindings = [...merged.values()];
  run.executions.forEach((arm,index) => {
    arm.prometheus_observations = plans[index]!;
    for (const probe of probes) for (const query of probe.summaries) {
      arm.registry[`prometheus_query.${query.name}`] = {unit: query.unit, side: "resource", category: query.category, value_kind: "scalar",
        source: "prometheus_query", description: query.description ?? "", labels: probe.scope === "workload" ? ["service","workload"] : probe.scope === "service" ? ["service"] : ["operation","service"]};
    }
  });
}

export interface MetricCollectionEvent {
  run_id: string; arm_run_id: string; metric: string; window_id: string | null;
  state: "started" | "observed" | "missing" | "error"; error?: string;
}
export interface CollectionOptions {
  signal?: AbortSignal;
  /** Concurrent query requests; the borrowed client's own pool remains authoritative. */
  concurrency?: number;
  onProgress?(event: MetricCollectionEvent): void;
}

/**
 * @spec Collection cannot change request facts, evaluations or Run execution status.
 * @spec Each attempt replaces only its own projections; failed reads cannot retain stale values.
 */
export async function collectMetrics(run: Run, sources: readonly MetricDBReader[], options: CollectionOptions = {}): Promise<void> {
  const concurrency = options.concurrency ?? 4;
  if (!Number.isSafeInteger(concurrency) || concurrency < 1) throw new Error("Metric concurrency must be a positive integer");
  const clients = new Map(sources.map(source => [source.url, source]));
  if (clients.size !== sources.length) throw new Error("Metric sources must have unique URLs");
  const jobs = run.executions.flatMap(arm => (arm.prometheus_observations ?? []).map(query => ({arm, query})));
  for (const {arm, query} of jobs) {
    const source = clients.get(query.source_url);
    if (!source || source.kind !== query.datasource_kind) throw new Error("Saved metric source/backend does not match supplied access");
    if ((query.step_s !== null && (!(query.step_s > 0) || query.window_id !== null)) || query.result_labels.length) throw new Error("Unsupported Operation query shape or range step");
    if (query.step_s === null && !arm.windows.some(window => window.id === query.window_id)) throw new Error("Saved query references unknown window");
  }
  options.signal?.throwIfAborted();
  const fractions = new Set((run.observation_bindings ?? []).flatMap(binding =>
    binding.metrics.error_rate ? [binding.metrics.error_rate.metric] : []));
  for (const {arm, query} of jobs) {
    if (query.step_s !== null) delete arm.series?.[seriesId(query.metric,query.labels)];
    else delete arm.windows.find(window => window.id === query.window_id)!.probe_metrics[seriesId(query.metric,query.labels)];
    delete arm.probe_errors[query.probe];
    query.data = null; query.warnings = []; query.infos = []; query.collected_at_s = null;
    query.error = "collection not completed";
  }
  let next = 0;
  const work = async () => {
    while (next < jobs.length && !options.signal?.aborted) {
      const {arm,query} = jobs[next++]!;
      const event = {run_id: run.run_id, arm_run_id: arm.id, metric: query.metric, window_id: query.window_id};
      query.collected_at_s = Date.now()/1000;
      options.onProgress?.({...event, state: "started"});
      let state: MetricCollectionEvent["state"] = "missing";
      try {
        const client = clients.get(query.source_url)!;
        const result = query.step_s === null ? await client.query(query.expression, query.end_s, options.signal)
          : await client.queryRange(query.expression, query.start_s, query.end_s, query.step_s, options.signal);
        query.data = result.data; query.warnings = result.warnings; query.infos = result.infos; query.error = null;
        const address = seriesId(query.metric,query.labels);
        if (query.step_s !== null) {
          const points = projectSeries(query, arm.started_at_s!, fractions.has(`${address}.value`));
          if (points.length) {
            (arm.series ??= {})[address] = points;
            state = "observed";
          }
        } else {
          const summary = projectObservation(query,fractions.has(`${address}.value`));
          if (summary) {
            arm.windows.find(window => window.id === query.window_id)!.probe_metrics[address] = summary;
            state = "observed";
          }
        }
      } catch (error) {
        query.error = error instanceof Error ? error.message : "Metric query failed";
        state = "error";
      }
      options.onProgress?.({...event, state, ...(query.error ? {error: query.error} : {})});
    }
  };
  const workers = await Promise.allSettled(Array.from({length: Math.min(concurrency,jobs.length)}, work));
  for (const arm of run.executions) {
    const probes = new Set((arm.prometheus_observations ?? []).map(query => query.probe));
    for (const probe of probes) {
      const queries = arm.prometheus_observations!.filter(query => query.probe === probe);
      const failures = queries.filter(query => query.error);
      if (failures.length) arm.probe_errors[probe] = {failures: failures.length, ticks: queries.length, last: failures.at(-1)!.error};
    }
  }
  const failedWorker = workers.find(result => result.status === "rejected");
  if (failedWorker?.status === "rejected") throw failedWorker.reason;
  options.signal?.throwIfAborted();
}
