import { Graph, NodeKind, RelationKind, type Relation } from "@compforge/harness-common";
import type { Run, ArmRun, Window } from "./model";
import { serializeLoadPlan } from "./load";
import { RED_UNITS, type MetricBinding, type ScalarSummary } from "./observe/model";
import { seriesId } from "./observe/metric";

export interface CallGraphMetric {
  value: number | "+Inf" | null; unit: string; status: "observed" | "missing" | "error";
  metric_ref: string | null; reason: string | null; description: string; semantics: string;
  histogram: ScalarSummary["histogram"];
  queries: {expression: string; source_url: string; datasource_kind: string; start_s: number;
    end_s: number; collected_at_s: number | null; error: string | null; warnings: string[]}[];
}
export interface CallGraphNode {
  id: string; kind: string;
  properties: Record<string, unknown> & {perf: {
    name: string; importance: string; service: string; method: string; path: string;
    response_mode: string | null; qps_semantics: string | null; metrics: Record<string, CallGraphMetric>;
  }};
}
export interface CallGraphWindow {
  id: string; kind: string; phase: string | null; name: string; start_s: number; end_s: number;
  complete: boolean; limited_s: number; nodes: CallGraphNode[];
}
export interface CallGraphSnapshot {
  schema_version: 2; topology_source: "declared"; run_id: string; experiment: string;
  created_at: string; status: string; environment: string; case_ids: string[];
  entry_ids: string[];
  graph_source: string | null; graph_version: string | null; relations: readonly Relation[];
  arms: {id: string; arm_id: string; started_at_s: number | null; load: ReturnType<typeof serializeLoadPlan>;
    resources: {cpu: string | null; memory: string | null; workers: number | null; replicas: number; extra: Record<string,string>}; windows: CallGraphWindow[]}[];
}
function metric(arm: ArmRun, window: Window, binding: MetricBinding | undefined, unit: string): CallGraphMetric {
  const address = binding?.metric;
  const queries = (arm.prometheus_observations ?? []).filter(query => query.window_id === window.id &&
    `${seriesId(query.metric,query.labels)}.value` === address);
  const evidence = queries.map(({expression, source_url, datasource_kind, start_s, end_s, collected_at_s, error, warnings}) =>
    ({expression, source_url, datasource_kind, start_s, end_s, collected_at_s, error, warnings}));
  const summary = address?.endsWith(".value") ? window.probe_metrics[address.slice(0,-6)] : undefined;
  const failed = queries.some(query => query.error && !(Array.isArray(query.data?.result) &&
    !query.data.result.length && !query.warnings.length));
  const observed = !failed && summary?.kind === "scalar" && (summary.value === "+Inf" || Number.isFinite(summary.value));
  return {value: observed ? summary!.value : null, unit: binding?.unit ?? unit,
    status: failed ? "error" : observed ? "observed" : "missing", metric_ref: address ?? null,
    reason: failed ? "query_error" : observed ? null : !address ? "unbound" : queries.some(query => query.collected_at_s !== null) ? "no_samples" : "not_collected",
    queries: evidence, description: binding?.description ?? "", semantics: binding?.semantics ?? "",
    histogram: observed ? summary?.histogram ?? null : null};
}
/**
 * @spec Derive schema 2 from saved facts only; preserve zero, missing, errors and histogram overflow.
 * @rule Declared edges and aggregate metric scope do not prove observed calls or per-Case causality.
 */
export function buildCallgraph(run: Run): CallGraphSnapshot | undefined {
  if (!run.graph) return undefined;
  const graph = Graph.fromJSON(run.graph);
  if (graph.environment !== run.environment) throw new Error("Graph environment must match Run");
  const bindings = new Map((run.observation_bindings ?? []).filter(binding => !binding.workload).map(binding => [binding.target,binding]));
  const owners = new Map(graph.relations.filter(r => r.kind === RelationKind.BelongsTo)
    .map(r => [r.source, decodeURIComponent(r.target.replace(/^service:/,""))]));
  const incoming = new Set(graph.calls.map(relation => relation.target));
  const entries = run.entry_ids?.length ? [...new Set(run.entry_ids)]
    : graph.nodes.filter(node => node.kind === NodeKind.Operation && !incoming.has(node.id)).map(node => node.id);
  for (const id of entries) if (graph.node(id)?.kind !== NodeKind.Operation) throw new Error(`Invalid entry Operation: ${id}`);
  const operations = graph.nodes.filter(node => node.kind === NodeKind.Operation);
  return {schema_version: 2, topology_source: "declared", run_id: run.run_id, experiment: run.experiment,
    created_at: run.created_at, status: run.status, environment: run.environment, case_ids: [...(run.case_ids ?? [])],
    entry_ids: entries,
    graph_source: graph.source ?? null, graph_version: graph.version ?? null, relations: graph.calls,
    arms: run.executions.map(arm => ({id: arm.id, arm_id: arm.arm.id, started_at_s: arm.started_at_s ?? null,
      load: serializeLoadPlan(arm.arm.load), resources: {cpu: arm.arm.resources.cpu ?? null, memory: arm.arm.resources.memory ?? null,
        workers: arm.arm.resources.workers ?? null, replicas: arm.arm.resources.replicas ?? 1, extra: arm.arm.resources.extra ?? {}},
      windows: [...arm.windows].sort((a,b) => ({full:0,phase:1,custom:2}[a.kind]-{full:0,phase:1,custom:2}[b.kind]) || a.start_s-b.start_s)
        .map(window => ({id: window.id, kind: window.kind, phase: window.phase ?? null, name: window.name,
          start_s: window.start_s, end_s: window.end_s, complete: window.complete, limited_s: window.limited_s ?? 0,
          nodes: operations.map(node => {
            const binding = bindings.get(node.id), roles = new Set([...Object.keys(RED_UNITS),...Object.keys(binding?.metrics ?? {})]);
            const properties = node.properties ?? {};
            return {id: node.id, kind: node.kind, properties: {...properties, perf: {
              name: decodeURIComponent(node.id.slice(node.id.indexOf("/")+1)),
              importance: entries.includes(node.id) ? "high" : String(properties.importance ?? "medium"),
              service: owners.get(node.id)!, method: binding?.method ?? String(properties.method ?? ""),
              path: binding?.path ?? String(properties.path ?? ""), response_mode: binding ? binding.response_mode ?? null : (typeof properties.response_mode === "string" ? properties.response_mode : null),
              qps_semantics: binding?.metrics.qps?.semantics ?? null,
              metrics: Object.fromEntries([...roles].map(role => [role,metric(arm,window,binding?.metrics[role], RED_UNITS[role as keyof typeof RED_UNITS] ?? "")]))}}};
          })}))}))};
}

/** Pure view of the saved full graph; shared descendants/cycles retain original identities. */
export function entryCallgraph(snapshot: CallGraphSnapshot): CallGraphSnapshot {
  const reachable = new Set(snapshot.entry_ids), pending = [...reachable];
  while (pending.length) {
    const id = pending.pop()!;
    for (const edge of snapshot.relations) if (edge.source === id && !reachable.has(edge.target)) {
      reachable.add(edge.target); pending.push(edge.target);
    }
  }
  return {...snapshot, relations: snapshot.relations.filter(edge => reachable.has(edge.source) && reachable.has(edge.target)),
    arms: snapshot.arms.map(arm => ({...arm, windows: arm.windows.map(window => ({...window,
      nodes: window.nodes.filter(node => reachable.has(node.id))}))}))};
}
