import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Run } from "../model";
import { apiRows, apiHeader, reportSections } from "./sections";
import { escape, section, table } from "./view";
import { buildCallgraph, entryCallgraph, type CallGraphSnapshot, type CallGraphWindow } from "../callgraph";
import { graphScripts, tabScript, reportStyle, sort } from "./assets";

function number(value: unknown): string {
  return value === null || value === undefined ? "N/A" : typeof value === "number" ? Number(value.toPrecision(4)).toString() : String(value);
}
const missingReasons: Record<string,string> = {
  unbound: "No metric probe is bound",
  not_collected: "Query has not been collected",
  empty_result: "Query returned no result; metric absence, insufficient samples and zero activity require separate evidence",
  no_observations: "Histogram contains zero observed events in this window",
  no_samples: "No usable samples; cause is not established",
  query_error: "Query or result validation failed; inspect the recorded error and warnings",
};
function frameNodes(window: CallGraphWindow, colors: Map<string,string>) {
  const values = window.nodes.map(node => node.properties.perf.metrics.qps?.value).filter((value): value is number =>
    typeof value === "number" && Number.isFinite(value) && value >= 0);
  const low = Math.min(...values), high = Math.max(...values);
  return window.nodes.map(node => {
    const props = node.properties.perf, metrics = props.metrics, qps = metrics.qps?.value;
    const details = [["Operation",node.id],["Service",props.service],["Path",props.path],["Method",props.method],
      ["Response mode",props.response_mode ?? "unbound"],["Window",`${window.id}: ${window.start_s}–${window.end_s}s`],
      ["QPS semantics",props.qps_semantics ?? "unbound"]];
    for (const [role,metric] of Object.entries(metrics)) {
      details.push([role,`${number(metric.value)} ${metric.unit} (${metric.status})`]);
      if (metric.description) details.push([`${role} description`,metric.description]);
      if (metric.reason) details.push([`${role} reason`,missingReasons[metric.reason] ?? metric.reason]);
      if (metric.metric_ref) details.push([`${role} metric`,metric.metric_ref]);
      if (metric.histogram) details.push([`${role} histogram`,JSON.stringify(metric.histogram)]);
      for (const query of metric.queries) {
        details.push(["PromQL",query.expression],["Source",`${query.datasource_kind} ${query.source_url}`],
          ["Query range",`${query.start_s}–${query.end_s}`]);
        if (query.error) details.push(["Query error",query.error]);
        if (query.warnings.length) details.push(["Query warnings",query.warnings.join("; ")]);
      }
    }
    const observedQps = typeof qps === "number" && Number.isFinite(qps) && qps >= 0;
    const error = metrics.error_rate?.value;
    return {id:node.id, group:props.service, importance:props.importance,
      label:`${props.name}\nQPS ${number(qps)}\np95 ${number(metrics.p95?.value)} s\nerr ${typeof error === "number" ? number(error*100)+"%" : "N/A"}`,
      details,tooltip:details.map(([key,value]) => `${key}: ${value}`).join("\n"),color:colors.get(props.service),
      comparison_value:observedQps ? qps : null,
      diameter: observedQps && high > low ? Math.sqrt(88**2+(qps-low)/(high-low)*(160**2-88**2)) : 112};
  });
}
function graphView(snapshot: CallGraphSnapshot, run: Run): string {
  const palette = ["#dbeafe","#dcfce7","#fef3c7","#ede9fe","#fce7f3","#cffafe"];
  const services = [...new Set(snapshot.arms.flatMap(arm => arm.windows.flatMap(window => window.nodes.map(node => node.properties.perf.service))))].sort();
  const colors = new Map(services.map((service,index) => [service,palette[index%palette.length]!]));
  const edges = snapshot.relations.map(relation => ({id:relation.id,source:relation.source,target:relation.target,
    label:relation.kind === "may_call" ? "may_call" : "call",dashed:relation.kind === "may_call"}));
  const subset = entryCallgraph(snapshot);
  const panels: string[] = [];
  const frames = snapshot.arms.flatMap((arm, armIndex) => arm.windows.flatMap((window, windowIndex) => {
    const scoped = subset.arms[armIndex]!.windows[windowIndex]!;
    const label = `${arm.arm_id} / ${window.name} [${window.start_s.toFixed(3)}–${window.end_s.toFixed(3)}s]`;
    const execution = run.executions.find(a => a.id === arm.id)!;
    const rows = apiRows(execution, execution.windows.find(w => w.id === window.id));
    const content = rows.length ? table(apiHeader, rows) : "<p>No observations in this view.</p>";
    for (let i = 0; i < (snapshot.entry_ids.length ? 2 : 1); i++) {
      panels.push(`<div data-graph-frame="${panels.length}"${panels.length ? " hidden" : ""}>${content}</div>`);
    }
    return [
      ...(snapshot.entry_ids.length ? [{label:`Case entry / ${label}`, group:"Case entry",
        nodes:frameNodes(scoped,colors), edges:edges.filter(edge => subset.relations.some(r => r.id === edge.id)), entry_ids:snapshot.entry_ids}] : []),
      {label:`Full graph / ${label}`, group:"Full graph", nodes:frameNodes(window,colors), edges, entry_ids:[]},
    ];
  }));
  if (!frames.length) return "<p>No observation windows were recorded.</p>";
  const payload = JSON.stringify({title:"Operation metrics",frames,group_filter_label:"Service",importance_filter:true,comparison_filter_label:"QPS"}).replaceAll("<","\\u003c");
  return `<div id="callgraph" class="graph-view"><details class="graph-table"><summary>完整窗口指标明细</summary>${panels.join("")}</details></div>
${graphScripts()}<script>rkGraph(document.getElementById("callgraph"),${payload});</script>`;
}
/** Render the eval-perf report from persisted facts only. */
export function renderPerfReport(run: Run, options: { issues?: readonly string[]; facetOrder?: Readonly<Record<string, readonly string[]>> } = {}): string {
  const snapshot = buildCallgraph(run);
  const report = reportSections(run, options.facetOrder ?? {}, options.issues ?? []);
  let body = report;
  if (snapshot) {
    const scope = run.case_ids?.length === 1 ? "Case: " + escape(run.case_ids[0]) : "Aggregate case scope; metrics are not attributed to individual cases.";
    const graph = section("Call Graph", `<p>${scope}</p><p>Operation nodes; service is auxiliary information. Solid: call, dashed: may_call. Metrics describe the selected server/window, including other matching traffic. Node traffic does not prove a particular edge executed. N/A means missing observation, not zero. Latency percentiles cannot be summed along paths. Circle area scales with the current window’s QPS range; missing QPS uses the default size. Hover for details.</p>${graphView(snapshot,run)}`);
    body = `<div class="report-tabs" role="tablist" aria-label="Report views">
<button type="button" role="tab" id="rk-tab-0" aria-controls="rk-panel-0" aria-selected="true" tabindex="0">Report</button>
<button type="button" role="tab" id="rk-tab-1" aria-controls="rk-panel-1" aria-selected="false" tabindex="-1">Callgraph</button>
</div><div role="tabpanel" id="rk-panel-0" aria-labelledby="rk-tab-0">${report}</div>
<div role="tabpanel" id="rk-panel-1" aria-labelledby="rk-tab-1" data-wide="true" hidden>${graph}</div>${tabScript()}`;
  }
  const title = "perf report — " + escape(run.service);
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><style>${reportStyle}</style><script>${sort}</script></head><body><h1>${title}</h1><div class="meta"><span><b>service:</b> ${escape(run.service)}</span></div>${body}</body></html>`;
}
export function writePerfReport(run: Run, directory: string): {html:string; callgraph?:string} {
  mkdirSync(directory,{recursive:true});
  const html = join(directory,"report.html"), snapshot = buildCallgraph(run);
  writeFileSync(html,renderPerfReport(run));
  if (!snapshot) return {html};
  const callgraph = join(directory,"callgraph.json");
  writeFileSync(callgraph,JSON.stringify(snapshot,null,2));
  return {html,callgraph};
}
