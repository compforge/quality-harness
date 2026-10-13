import echartsSource from "echarts/dist/echarts.min.js" with { type: "text" };
import { formatMs } from "../kinds/base";
import type { NodeView, View } from "./model";
import { renderTraceHtml } from "./interactive";
const esc = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const json = (value: unknown) => `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
const table = (headers: string[], rows: unknown[][]) => `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;

function renderNodeHtml(view: NodeView): string {
  let body = `<h1>${esc(view.node.name)} · ${esc(view.view)}</h1><p>trace=${esc(view.trace_id)} · node=${esc(view.node.node_id)} · ${formatMs(view.node.duration_ms)}</p>`;
  let script = '';
  if (view.view === 'default') body += `<h2>Facts</h2>${json(view.node.facts)}<h2>Details</h2>${json(view.node.details)}<h2>Findings</h2>${json(view.node.findings)}<h2>Spans</h2>${json(view.spans)}`;
  else {
    body += `<p>${esc(view.timing?.status ?? 'not_prepared')} ${esc(view.timing?.error)}</p>`;
    if (view.timing?.window) body += `<p>请求开始 → 节点结束 · ${formatMs(view.timing.window.elapsed_ms)} wall-clock</p><p>嵌套及并行调用可重叠，累计耗时不代表独占的等待贡献。</p>`;
    if (view.view === 'measurements') {
      body += table(['Measurement', 'Scope', 'Kind', 'Status', 'Values / units', 'Evidence'], view.measurements.map(row => [row.kind, row.scope, row.call_kind, row.status, JSON.stringify({ values: row.values, units: row.units, error: row.error }), JSON.stringify(row.evidence)]));
      if (view.groups) body += `<h2>Call groups</h2><p>${view.groups.items.length}/${view.groups.total} groups · offset=${view.groups.offset} · next=${view.groups.next_offset ?? 'none'}</p>` + table(['Kind', 'Service', 'Target', 'Operation', 'Count', 'Sum', 'Average', 'Max'], view.groups.items.map(g => [g.kind, g.service, g.target, g.name, g.count, formatMs(g.total), formatMs(g.average), formatMs(g.max)]));
      if (view.groups?.items.length) {
        body += '<div id="groups" style="height:65vh"></div>';
        script = `${echartsSource}\nconst groups=${JSON.stringify(view.groups.items).replace(/</g, '\\u003c')};const chart=echarts.init(document.getElementById('groups'));chart.setOption({animation:false,grid:{left:260,right:50,top:40,bottom:50},xAxis:{type:'value',name:'累计耗时(ms)'},yAxis:{type:'category',inverse:true,data:groups.map(g=>g.service+' '+g.name),axisLabel:{width:240,overflow:'truncate'}},dataZoom:[{type:'slider',yAxisIndex:0,startValue:0,endValue:Math.min(groups.length-1,15)}],series:[{type:'bar',data:groups.map(g=>g.total)}]});window.addEventListener('resize',()=>chart.resize());`;
      }
    } else if (view.calls) {
      body += `<p>${view.calls.items.length}/${view.calls.total} calls · offset=${view.calls.offset} · next=${view.calls.next_offset ?? 'none'}</p><div id="timeline" style="height:65vh"></div>`;
      body += table(['Operation', 'Start', 'Duration', 'Node', 'Spans'], view.calls.items.map(c => [c.name, formatMs(c.start), formatMs(c.duration), c.node_id, c.span_ids.join(', ')]));
      if (view.calls.items.length) script = `${echartsSource}\nconst view=${JSON.stringify(view).replace(/</g, '\\u003c')};const rows=view.calls.items;const chart=echarts.init(document.getElementById('timeline'));chart.setOption({animation:false,grid:{left:260,right:40,top:35,bottom:60},tooltip:{formatter:p=>rows[p.dataIndex].name.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))+' · '+rows[p.dataIndex].duration+'ms'},xAxis:{type:'value',min:0,max:Math.max(view.timing.window.elapsed_ms,.001),position:'top'},yAxis:{type:'category',inverse:true,data:rows.map(c=>c.name),axisLabel:{width:240,overflow:'truncate'}},dataZoom:[{type:'slider',xAxisIndex:0},{type:'slider',yAxisIndex:0,filterMode:'none',startValue:0,endValue:Math.min(rows.length-1,15)}],series:[{type:'custom',encode:{x:[1,2],y:0},renderItem:(p,a)=>{const s=a.coord([a.value(1),a.value(0)]),e=a.coord([a.value(2),a.value(0)]);const shape=echarts.graphic.clipRectByRect({x:s[0],y:s[1]-7,width:Math.max(2,e[0]-s[0]),height:14},p.coordSys);return shape&&{type:'rect',shape,style:{fill:'#3b82f6'}}},data:rows.map((c,i)=>[i,c.start,c.end])}]});window.addEventListener('resize',()=>chart.resize());`;
    }
  }
  return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(view.node.name)}</title><style>body{font:14px system-ui;margin:24px;color:#172033}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>${body}<script>${script.replace(/<\/script/gi, '<\\/script')}</script></body></html>`;
}
export function renderHtml(view: View): string {
  return view.scope === 'trace' ? renderTraceHtml([view]) : renderNodeHtml(view);
}
