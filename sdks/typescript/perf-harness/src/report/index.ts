import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Run } from "../model";
import { stages, observesOnly } from "../load";
import { buildCallgraph, entryCallgraph, type CallGraphSnapshot, type CallGraphWindow } from "../callgraph";
import { graphScripts, tabScript } from "./assets";

function escape(value: unknown): string {
  return String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
}
function number(value: unknown): string {
  return value === null || value === undefined ? "N/A" : typeof value === "number" ? Number(value.toPrecision(4)).toString() : String(value);
}
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
      if (metric.reason) details.push([`${role} reason`,metric.reason]);
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
function graphView(snapshot: CallGraphSnapshot): string {
  const palette = ["#dbeafe","#dcfce7","#fef3c7","#ede9fe","#fce7f3","#cffafe"];
  const services = [...new Set(snapshot.arms.flatMap(arm => arm.windows.flatMap(window => window.nodes.map(node => node.properties.perf.service))))].sort();
  const colors = new Map(services.map((service,index) => [service,palette[index%palette.length]!]));
  const edges = snapshot.relations.map(relation => ({id:relation.id,source:relation.source,target:relation.target,
    label:relation.kind === "may_call" ? "may_call" : "call",dashed:relation.kind === "may_call"}));
  const subset = entryCallgraph(snapshot);
  const frames = snapshot.arms.flatMap((arm, armIndex) => arm.windows.flatMap((window, windowIndex) => {
    const scoped = subset.arms[armIndex]!.windows[windowIndex]!;
    const label = `${arm.arm_id} / ${window.name} [${window.start_s.toFixed(3)}–${window.end_s.toFixed(3)}s]`;
    return [
      ...(snapshot.entry_ids.length ? [{label:`Case entry / ${label}`, group:"Case entry",
        nodes:frameNodes(scoped,colors), edges:edges.filter(edge => subset.relations.some(r => r.id === edge.id)), entry_ids:snapshot.entry_ids}] : []),
      {label:`Full graph / ${label}`, group:"Full graph", nodes:frameNodes(window,colors), edges, entry_ids:[]},
    ];
  }));
  if (!frames.length) return "<p>No observation windows were recorded.</p>";
  const payload = JSON.stringify({title:"Operation metrics",frames,group_filter_label:"Service",importance_filter:true,comparison_filter_label:"QPS"}).replaceAll("<","\\u003c");
  return `<div id="callgraph" class="graph-view"></div>
${graphScripts()}<script>rkGraph(document.getElementById("callgraph"),${payload});</script>`;
}
/** @spec Render only persisted facts; no queries, load generation or Python runtime. */
export function renderPerfReport(run: Run, options: { issues?: readonly string[]; facetOrder?: Readonly<Record<string, readonly string[]>> } = {}): string {
  const snapshot = buildCallgraph(run);
  const rows = run.executions.flatMap(arm => arm.windows.filter(window => window.request).map(window => {
    const stats = window.request;
    return `<tr>${[arm.arm.id,window.name,`${window.start_s.toFixed(3)}–${window.end_s.toFixed(3)}`,window.complete,
      number(stats?.dispatch_rps),number(stats?.throughput_rps),number(stats?.p95_ms),number(stats?.error_rate),arm.stop.reason]
      .map(value => `<td>${escape(value)}</td>`).join("")}</tr>`;
  })).join("");
  const cases = run.executions.flatMap(arm => arm.windows.filter(w => w.kind === "phase" && w.phase === "hold").flatMap(window =>
    Object.entries(window.by_case).map(([id,stats]) => `<tr>${[arm.arm.id,window.name,id,stats.completed,number(stats.throughput_rps),number(stats.p95_ms),number(stats.metrics.first_token_ms?.p95)].map(value => `<td>${escape(value)}</td>`).join("")}</tr>`))).join("");
  const facets = run.executions.flatMap(arm => arm.windows.filter(w => w.kind === "phase" && w.phase === "hold").flatMap(window =>
    Object.entries(window.by_facet).flatMap(([facet,values]) => Object.entries(values).sort(([a],[b]) => {
      const order = options.facetOrder?.[facet];
      const rank = (value:string) => order?.includes(value) ? order.indexOf(value) : Infinity;
      return (rank(a)-rank(b)) || a.localeCompare(b);
    }).map(([value,stats]) => `<tr>${[arm.arm.id,window.name,facet,value,stats.completed,number(stats.throughput_rps),number(stats.p95_ms)].map(item => `<td>${escape(item)}</td>`).join("")}</tr>`)))).join("");
  const windows = run.executions.flatMap(arm => arm.windows.filter(window => window.kind === "full").map(window =>
    `<li>Injection QPS ${escape(stages(arm.arm.load).filter(s=>s.kind === "hold").map(s=>s.request_rate).join(" → "))}${observesOnly(arm.arm.load) ? "" : ` · max inflight ${escape(arm.arm.load.max_inflight)}`} · observed ${number(window.end_s-window.start_s)} s · ${escape(arm.stop.reason)}</li>`)).join("");
  const metricRows = snapshot?.arms.flatMap(arm => arm.windows.flatMap(window => window.nodes.map(node => {
    const metrics = node.properties.perf.metrics;
    return `<tr>${[arm.arm_id,node.id,window.name, ...["qps","p95","error_rate"].map(role => {
      const metric = metrics[role];
      return metric ? `${number(metric.value)} ${metric.unit} (${metric.status})` : "N/A";
    })].map(value => `<td>${escape(value)}</td>`).join("")}</tr>`;
  }))).join("") ?? "";
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Perf ${escape(run.run_id)}</title><style>
:root{--line:#d0d7de;--accent:#0969da;--muted:#656d76}
[hidden]{display:none!important}.report-tabs{display:flex;gap:8px;border-bottom:1px solid var(--line);margin:20px 0}
.report-tabs [role="tab"]{padding:10px 20px;border:0;border-bottom:3px solid transparent;background:none;font:inherit;cursor:pointer;color:var(--muted)}
.report-tabs [aria-selected="true"]{color:var(--accent);border-color:var(--accent);font-weight:600}
.report-tabs [role="tab"]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
body{font:14px system-ui;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #d9e0e6;text-align:left}th{background:#f4f7f9}
</style><h1>Perf ${escape(run.experiment)}</h1><p>Run ${escape(run.run_id)} · ${escape(run.environment)} · execution ${escape(run.status)}</p>
<div class="report-tabs" role="tablist" aria-label="Report views">
<button type="button" role="tab" id="rk-tab-0" aria-controls="rk-panel-0" aria-selected="true" tabindex="0">Report</button>
<button type="button" role="tab" id="rk-tab-1" aria-controls="rk-panel-1" aria-selected="false" tabindex="-1">Callgraph</button>
</div><div role="tabpanel" id="rk-panel-0" aria-labelledby="rk-tab-0">
<h2>Observation</h2><ul>${windows}</ul><p>Injection targets are configuration; server throughput describes actual matching traffic.</p>
${options.issues?.length ? `<h2>Metric coverage</h2><ul>${options.issues.map(issue => `<li>${escape(issue)}</li>`).join("")}</ul>` : ""}
${metricRows ? `<details open><summary>Operation metrics</summary><table><tr><th>Arm</th><th>Operation</th><th>Window</th><th>Observed QPS</th><th>p95</th><th>Error ratio</th></tr>${metricRows}</table></details>` : ""}
${rows ? `<details open><summary>Injected request windows</summary><table><tr>${["Arm","Window","Seconds","Complete","Dispatch rps","Completion rps","p95 ms","Error ratio","Stop"].map(value => `<th>${value}</th>`).join("")}</tr>${rows}</table></details>` : ""}
${cases ? `<h2>By Case</h2><table><tr><th>Arm</th><th>Window</th><th>Case</th><th>Completed</th><th>Completion rps</th><th>p95 ms</th><th>First token p95 ms</th></tr>${cases}</table>` : ""}
${facets ? `<h2>By Facet</h2><table><tr><th>Arm</th><th>Window</th><th>Facet</th><th>Value</th><th>Completed</th><th>Completion rps</th><th>p95 ms</th></tr>${facets}</table>` : ""}
<p>Raw evidence: run.json, requests.jsonl and evaluations.json retain window boundaries and individual request facts.</p>
</div><div role="tabpanel" id="rk-panel-1" aria-labelledby="rk-tab-1" data-wide="true" hidden>
<h2>Callgraph</h2><p>${run.case_ids?.length === 1 ? `Case: ${escape(run.case_ids[0])}` : "Aggregate case scope; metrics are not attributed to individual cases."}</p>
<p>Declared topology with server/window metrics, including other matching traffic. Solid: call; dashed: may_call. Node traffic does not prove an edge executed. N/A is missing evidence, not zero. Latency percentiles cannot be summed along paths.</p>
<div data-wide="true">${snapshot ? graphView(snapshot) : "<p>No operation graph was declared.</p>"}</div></div>${tabScript()}</html>`;
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
