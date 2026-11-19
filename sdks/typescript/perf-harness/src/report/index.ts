import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Run } from "../model";
import { apiRows, apiHeader, reportSections } from "./sections";
import { escape, section, table } from "./view";
import { buildCallgraph, entryCallgraph, type CallGraphMetric, type CallGraphSnapshot, type CallGraphWindow } from "../callgraph";
import { chartScript, graphScripts, tabScript, reportStyle, sort } from "./assets";

import { charts } from "./charts";
import navigationStyle from "./navigation.css" with { type: "text" };
import navigation from "./navigation.js" with { type: "text" };

function metricValue(metric: CallGraphMetric | undefined, suffix = "", scale = 1): string {
  if (metric?.status === "error") return "error";
  if (typeof metric?.value !== "number") return "N/A";
  return Number((metric.value * scale).toPrecision(4)).toString() + suffix;
}

function frameNodes(window: CallGraphWindow, colors: Map<string,string>) {
  const values = window.nodes.map(node => node.properties.perf.metrics.qps?.value).filter((value): value is number =>
    typeof value === "number" && Number.isFinite(value) && value >= 0);
  const low = Math.min(...values), high = Math.max(...values);
  return window.nodes.map(node => {
    const props = node.properties.perf, metrics = props.metrics, qps = metrics.qps?.value;
    const red = [
      ["QPS", metricValue(metrics.qps)],
      ["p95", metricValue(metrics.p95, " s")],
      ["err", metricValue(metrics.error_rate, "%", 100)],
    ];
    const observedQps = typeof qps === "number" && Number.isFinite(qps) && qps >= 0;
    return {id:node.id, group:props.service, importance:props.importance,
      label:[props.name, ...red.map(([key, value]) => key + " " + value)].join("\n"),
      details: [["Operation", node.id]], tooltip: node.id, color:colors.get(props.service),
      comparison_value:observedQps ? qps : null,
      diameter: observedQps && high > low ? Math.sqrt(88**2+(qps-low)/(high-low)*(160**2-88**2)) : 112};
  });
}
function graphView(snapshot: CallGraphSnapshot, run: Run, description: string): string {
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
  const payload = JSON.stringify({title:"Operation metrics",frames,group_filter_label:"Service",importance_filter:true,comparison_filter_label:"QPS"}).replaceAll("<","\\u003c");
  const empty = "<p>No observation windows were recorded.</p>";
  const graph = section("Callgraph", description + (frames.length ? '<div data-graph-controls></div>' : empty));
  const metrics = `<details class="graph-table"><summary>完整窗口指标明细</summary>${panels.join("") || empty}</details>`;
  return `<div class="perf-layout"><nav id="callgraph-menu" class="perf-menu" aria-label="调用图章节"></nav><div id="callgraph" class="perf-content graph-view">${graph}${metrics}</div></div><script>${navigation}</script>
${frames.length ? graphScripts() + `<script>rkGraph(document.getElementById("callgraph"),${payload});</script>` : ""}`;
}
/** Render the eval-perf report from persisted facts only. */
export function renderPerfReport(run: Run, options: { issues?: readonly string[]; facetOrder?: Readonly<Record<string, readonly string[]>> } = {}): string {
  const snapshot = buildCallgraph(run);
  const sections = reportSections(run, options.facetOrder ?? {}, options.issues ?? []);
  const layout = (menu: string, content: string) =>
    `<div class="perf-layout"><nav id="${menu}-menu" class="perf-menu" aria-label="${menu}"></nav><div class="perf-content">${content}</div></div><script>${navigation}</script>`;
  const views = [{name: "Overview", content: layout("overview", sections)}];
  const { trends, resources } = charts(run);
  if (trends) views.push({name: "Metrics", content: layout("metrics", trends)});
  views.push({name: "Resources", content: layout("resources", resources || "<p>本次运行没有资源观测记录。</p>")});
  if (snapshot) {
    const scope = run.case_ids?.length === 1 ? "Case: " + escape(run.case_ids[0]) : "Aggregate case scope; metrics are not attributed to individual cases.";
    const graph = graphView(snapshot, run, `<p>${scope}</p><p>Operation nodes; service is auxiliary information. Solid: call, dashed: may_call. Metrics describe the selected server/window, including other matching traffic. Node traffic does not prove a particular edge executed. N/A marks a metric without a finite value. Query failures show error. See run.json for query diagnostics. Latency percentiles cannot be summed along paths. Circle area scales with the current window’s QPS range; missing QPS uses the default size. Hover for the full Operation ID. Open the window metrics table for detailed values.</p>`);
    views.push({name: "Callgraph", content: graph});
  }
  const tabs = views.map(({name}, i) => `<button type="button" role="tab" id="rk-tab-${i}" aria-controls="rk-panel-${i}" aria-selected="${i === 0}" tabindex="${i ? -1 : 0}">${name}</button>`);
  const panels = views.map(({content}, i) => `<div role="tabpanel" id="rk-panel-${i}" aria-labelledby="rk-tab-${i}" data-wide="true"${i ? " hidden" : ""}>${content}</div>`);
  const body = `<div class="report-tabs" role="tablist" aria-label="Report views">${tabs.join("")}</div>${panels.join("")}${tabScript()}`;
  const title = "perf report — " + escape(run.service);
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><style>${reportStyle}\n${navigationStyle}</style><script>${sort}</script>${trends || resources ? chartScript() : ""}</head><body><h1>${title}</h1><div class="meta"><span><b>service:</b> ${escape(run.service)}</span></div>${body}</body></html>`;
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
