import { httpEndpoint } from "../kinds/http";
import type { TraceContext } from "../model/context";
import type { Measurements } from "../model/measurement";
import browserSource from "./measurement-dialog.browser.js" with { type: "text" };

/**
 * @spec Report projections retain the measured call identities and their original intervals.
 * @why Aggregation and window clipping are view operations; neither changes analysis or HTTP pairing.
 */
export function measurementCallDetails(trace: TraceContext, measurements: Measurements) {
  const nodes = trace.view().by_id;
  const nodeCalls = new Map(measurements.sources.filter(call => call.kind !== "http").map(call => [call.node_id, `${call.kind}:${call.id}`]));
  return measurements.sources.map(call => {
    const node = nodes.get(call.node_id);
    const span = trace.spans.get(call.kind === "http" ? call.span_ids[0] ?? "" : node?.primary_span_id ?? "");
    const endpoint = span && (call.kind === "http" || call.kind === "service-api") ? httpEndpoint(span) : undefined;
    const serverRoute = call.kind === "http" ? call.span_ids.slice(1).map(id => trace.spans.get(id)).filter(item => item !== undefined).map(item => httpEndpoint(item!)).find(item => item !== undefined)?.[2] : undefined;
    const name = endpoint ? `${endpoint[0]} ${serverRoute ?? endpoint[2]}` : node?.name ?? span?.name ?? call.id;
    let parent = node?.parent_node_id;
    while (parent && !nodeCalls.has(parent)) parent = nodes.get(parent)?.parent_node_id;
    return {
      ...call, key: `${call.kind}:${call.id}`,
      parent: call.kind === "http" && nodeCalls.has(call.node_id) ? nodeCalls.get(call.node_id) : parent ? nodeCalls.get(parent) : undefined,
      name, target: endpoint?.[1] ?? "",
      service: (call.kind === "http" ? span?.service : node?.service) ?? node?.service ?? "",
    };
  });
}

export const MEASUREMENT_DIALOG_SCRIPT = browserSource;
export const MEASUREMENT_DIALOG_HTML = `<dialog id="measurement-dialog" aria-labelledby="measurement-title">
<div class="measurement-head"><div><h2 id="measurement-title"></h2><p id="measurement-window"></p></div><button id="measurement-close" aria-label="关闭耗时分析">关闭 ✕</button></div>
<div id="measurement-tabs" role="tablist" aria-label="耗时分析视图"><button id="measurement-summary-tab" role="tab" aria-controls="measurement-summary" aria-selected="true" data-measurement-tab="summary">调用聚合</button><button id="measurement-timeline-tab" role="tab" aria-controls="measurement-timeline" aria-selected="false" tabindex="-1" data-measurement-tab="timeline">调用时间线</button></div>
<p class="measurement-note">统计窗口内的调用耗时；嵌套与并行调用可重叠，累计耗时不代表独占的等待贡献。不同 kind 保留各自观测口径。</p>
<div id="measurement-summary" role="tabpanel" aria-labelledby="measurement-summary-tab"></div>
<div id="measurement-timeline" role="tabpanel" aria-labelledby="measurement-timeline-tab" hidden><div class="measurement-tools"><span id="measurement-filter"></span><button id="measurement-clear">清除筛选</button><button id="measurement-expand">全部展开</button><button id="measurement-fold">全部折叠</button><button id="measurement-reset">重置缩放</button></div><div class="measurement-timeline-scroll"><div id="measurement-chart"></div></div><div id="measurement-call-detail" aria-live="polite"></div></div>
<div id="measurement-other"></div></dialog>`;
export const MEASUREMENT_DIALOG_CSS = `
#measurement-dialog{box-sizing:border-box;width:94vw;max-width:1600px;height:90vh;max-height:94vh;padding:24px;border:1px solid #cbd5e1;border-radius:12px;color:#172033;background:#fff;overflow:auto;box-shadow:0 24px 80px #0f172a40}
#measurement-dialog [hidden]{display:none!important}
#measurement-dialog::backdrop{background:#0f172a80}
.measurement-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.measurement-head h2{margin:0 0 8px;font-size:20px;overflow-wrap:anywhere}.measurement-head p{margin:0 0 20px;color:#64748b;font-size:13px}
#measurement-dialog button{cursor:pointer;border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:5px;padding:6px 10px;font:inherit;font-size:12px}#measurement-dialog button:hover{background:#eff6ff}#measurement-dialog button:focus-visible{outline:2px solid #2563eb;outline-offset:2px}
#measurement-tabs{display:flex;gap:8px;border-bottom:1px solid #e2e8f0;padding-bottom:10px}#measurement-tabs [aria-selected=true]{background:#2563eb;color:#fff;border-color:#2563eb}
#measurement-dialog .measurement-note{margin:12px 0 18px}.measurement-tools{display:flex;gap:8px;align-items:center;margin:12px 0;flex-wrap:wrap}#measurement-filter{margin-right:auto;font-size:12px;color:#475569}
.measurement-operation{display:flex!important;flex-direction:column;gap:4px;text-align:left;border:0!important;background:transparent!important;max-width:540px;white-space:normal;overflow-wrap:anywhere}.measurement-operation small{color:#64748b}.measurement-total{min-width:170px}.measurement-bar{display:block;height:5px;background:#dbeafe;margin-top:5px;border-radius:3px}.measurement-bar i{display:block;background:#3b82f6;height:100%;border-radius:3px}
.measurement-timeline-scroll{max-height:55vh;overflow:auto;border:1px solid #e2e8f0;border-radius:6px}#measurement-chart{width:100%;min-width:640px;height:300px}#measurement-call-detail{font-size:12px;margin-top:12px;line-height:1.8;overflow-wrap:anywhere}#measurement-other .measurement-section{margin:12px 0}
`;
