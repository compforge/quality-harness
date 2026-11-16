import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Run } from "../model";
import { buildCallgraph, type CallGraphSnapshot, type CallGraphWindow } from "../callgraph";
import { graphScripts } from "./assets";

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
  const incoming = new Set(edges.map(edge => edge.target));
  const frames = snapshot.arms.flatMap(arm => arm.windows.map(window => ({label:`${arm.arm_id} / ${window.name} [${window.start_s.toFixed(3)}–${window.end_s.toFixed(3)}s]`,
    group:window.kind,nodes:frameNodes(window,colors),edges,entry_ids:window.nodes.filter(node => !incoming.has(node.id)).map(node => node.id)})));
  if (!frames.length) return "<p>No observation windows were recorded.</p>";
  const payload = JSON.stringify({title:"Operation metrics",frames,group_filter_label:"Service",importance_filter:true,comparison_filter_label:"QPS"}).replaceAll("<","\\u003c");
  const filter = (name: string) => `<details class="${name} graph-filter-control"><summary></summary><div class="filter-options"><div class="graph-filter-options"></div><button class="graph-filter-all">全部</button></div></details>`;
  return `<div id="callgraph" class="graph-view"><select class="graph-scope" aria-label="统计范围"></select>
<select class="graph-frame" aria-label="Graph view"></select>${filter("graph-filter")}${filter("graph-importance-filter")}${filter("graph-comparison-filter")}
<button class="graph-fit">Fit</button><button class="graph-copy">复制图片</button>
<span class="graph-filter-status" role="status"></span><span class="graph-comparison-status" role="status"></span>
<span class="graph-layout-status" role="status"></span><span class="graph-copy-status" role="status"></span>
<div class="graph-viewport"><div class="graph-canvas"></div><div class="graph-tooltip" role="tooltip" hidden></div>
<label class="graph-zoom">Zoom <input type="range" min="0" max="100" value="100" aria-label="Graph zoom"><output>100%</output></label></div>
<pre class="graph-details">Select a node for details.</pre></div>
${graphScripts()}<script>rkGraph(document.getElementById("callgraph"),${payload});</script>`;
}
/** @spec Render only persisted facts; no queries, load generation or Python runtime. */
export function renderPerfReport(run: Run): string {
  const snapshot = buildCallgraph(run);
  const rows = run.executions.flatMap(arm => arm.windows.map(window => {
    const stats = window.request;
    return `<tr>${[arm.arm.id,window.name,`${window.start_s.toFixed(3)}–${window.end_s.toFixed(3)}`,window.complete,
      number(stats?.dispatch_rps),number(stats?.throughput_rps),number(stats?.p95_ms),number(stats?.error_rate),arm.stop.reason]
      .map(value => `<td>${escape(value)}</td>`).join("")}</tr>`;
  })).join("");
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Perf ${escape(run.run_id)}</title><style>
body{font:14px system-ui;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #d9e0e6;text-align:left}th{background:#f4f7f9}
.graph-viewport{position:relative;margin-top:12px}.graph-canvas{height:78vh;min-height:500px;border:1px solid #d9e0e6}.graph-details{white-space:pre-wrap;overflow-wrap:anywhere}
.graph-tooltip{position:absolute;pointer-events:auto;white-space:pre-wrap;overflow-wrap:anywhere;max-width:min(560px,90%);max-height:65%;overflow:auto;background:white;border:1px solid #94a3b8;padding:8px;border-radius:4px;z-index:3}
.graph-zoom{position:absolute;bottom:12px;right:12px;background:white;padding:6px;display:flex;align-items:center;gap:6px}.graph-zoom output{min-width:4em}
.graph-filter-control{display:inline-block;position:relative;vertical-align:top;margin:0 4px}.graph-filter-control summary,button,select{font:inherit;cursor:pointer}
.filter-options{position:absolute;z-index:4;top:100%;left:0;width:max-content;max-width:80vw;background:white;border:1px solid #94a3b8;padding:8px}.graph-filter-options{max-height:260px;overflow:auto}
</style><h1>Perf ${escape(run.experiment)}</h1><p>Run ${escape(run.run_id)} · ${escape(run.environment)} · execution ${escape(run.status)}</p>
<details open><summary>Request windows</summary><table><tr>${["Arm","Window","Seconds","Complete","Dispatch rps","Completion rps","p95 ms","Error ratio","Stop"].map(value => `<th>${value}</th>`).join("")}</tr>${rows}</table></details>
<h2>Callgraph</h2><p>${run.case_ids?.length === 1 ? `Case: ${escape(run.case_ids[0])}` : "Aggregate case scope; metrics are not attributed to individual cases."}</p>
<p>Declared topology with server/window metrics, including other matching traffic. Solid: call; dashed: may_call. Node traffic does not prove an edge executed. N/A is missing evidence, not zero. Latency percentiles cannot be summed along paths.</p>
${snapshot ? graphView(snapshot) : "<p>No operation graph was declared.</p>"}</html>`;
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
