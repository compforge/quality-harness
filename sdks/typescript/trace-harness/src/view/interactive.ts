import renderJsonSource from "renderjson/renderjson.js" with { type: "text" };

import { lazyFeatures } from "../feature";
import { builtinFeatures } from "../feature/builtins";
import { FeatureRegistry } from "../feature/registry";
import type { AgentRunIR } from "../model/agent";
import type { TraceContext } from "../model/context";
import type { Finding, Node } from "../model/node";
import { DisplayName, nameProjections, type DisplayNode } from "./display";
import { agentRunRoots } from "./agent-run";
import { renderDisplay } from "./engine";
import { builtinFacets } from "./facets";
import { FacetRegistry } from "./registry";
import { toolNameDetail } from "./tool-name";

const ATTR_TRUNCATE = 4000;
const JSON_DECODE_LIMIT = 4;

function htmlEscape(value: unknown): string {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[char]!);
}

function structuredJson(value: unknown): unknown | undefined {
  let candidate = value;
  for (let depth = 0; depth < JSON_DECODE_LIMIT && typeof candidate === "string"; depth += 1) {
    const text = candidate.trim();
    if (!text) return undefined;
    try {
      candidate = JSON.parse(text) as unknown;
    } catch {
      return undefined;
    }
  }
  return candidate !== null && typeof candidate === "object" ? candidate : undefined;
}

function attrPayload(value: unknown): Record<string, unknown> {
  const json = structuredJson(value);
  if (json !== undefined) return { kind: "json", value: json };
  let text = typeof value === "string" ? value : (JSON.stringify(value) ?? String(value));
  if (text.length > ATTR_TRUNCATE) {
    text = `${text.slice(0, ATTR_TRUNCATE)}\n…[已截断，共 ${text.length} 字符；完整原文按 span_id 下钻]`;
  }
  return { kind: "text", value: text };
}

function displayPayload(
  context: TraceContext,
  display: DisplayNode,
  byId: Map<string, Node>,
  featureRegistry: FeatureRegistry,
): Record<string, unknown> {
  const children = display.children.map((child) => (
    displayPayload(context, child, byId, featureRegistry)
  ));
  const findings = display.findings.map((finding) => ({
    severity: finding.severity,
    source: finding.source,
    note: finding.note ?? "",
  }));
  const node = display.kind && display.node_ids.length ? byId.get(display.node_ids[0]!) : undefined;
  if (node) {
    const displayName = node.kind === "tool-call"
      ? String(node.facts.tool ?? display.name)
      : display.name;
    const argumentsValue = node.kind === "tool-call"
      ? context.raw_attr(String(node.facts.io_span ?? node.primary_span_id))["gen_ai.tool.call.arguments"]
      : undefined;
    const compact = typeof display.compact === "function"
      ? display
      : node.kind === "tool-call"
      ? new DisplayName(displayName, toolNameDetail(argumentsValue))
      : display;
    const features = Object.fromEntries(
      Object.entries(lazyFeatures(
        node,
        context.view(),
        (spanId) => context.raw_attr(spanId),
        featureRegistry,
      ))
        .filter(([, value]) => value !== undefined && value !== null && value !== "")
        .map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)]),
    );
    return {
      node_id: node.node_id,
      kind: display.kind,
      name: display.name,
      name_variants: nameProjections(compact, display.name),
      service: node.service ?? "",
      start_ms: node.start_ms,
      duration_ms: Number(node.facts.wall_ms ?? node.duration_ms),
      has_error: node.has_error,
      error: node.has_error ? context.error_text(node.error_anchor) : "",
      brief: display.brief.map((field) => `${field.label}=${field.value}`).join("  "),
      findings,
      facts: Object.fromEntries(
        Object.entries(node.facts)
          .filter(([key]) => !key.startsWith("_"))
          .map(([key, value]) => [key, String(value)]),
      ),
      features,
      span_ids: node.span_ids,
      primary_span_id: node.primary_span_id,
      error_span_ids: node.error_span_ids,
      folded: display.folded,
      children,
    };
  }
  const nodes = display.node_ids.map((id) => byId.get(id)).filter((item): item is Node => Boolean(item));
  const start = nodes.length ? Math.min(...nodes.map((item) => item.start_ms)) : 0;
  const end = nodes.length ? Math.max(...nodes.map((item) => item.end_ms)) : 0;
  return {
    node_id: display.node_ids.length ? `fold:${display.node_ids.slice(0, 3).join("·")}` : `fold:${display.name}`,
    kind: "",
    name: display.name,
    name_variants: nameProjections(display),
    service: "",
    start_ms: start,
    duration_ms: end - start,
    has_error: false,
    error: "",
    brief: display.brief.map((field) => `${field.label}=${field.value}`).join("  "),
    findings,
    facts: {},
    features: {},
    span_ids: [],
    primary_span_id: "",
    error_span_ids: [],
    folded: display.folded,
    children,
  };
}

function spanPayload(context: TraceContext, spanId: string): Record<string, unknown> {
  const span = context.spans.get(spanId)!;
  const attrs = Object.fromEntries(
    Object.entries(span.attrs).map(([key, value]) => [key, attrPayload(value)]),
  );
  return {
    service: span.service ?? "",
    operation: span.name,
    duration_ms: span.dur_ms,
    has_error: span.has_error,
    error: span.has_error ? context.error_text(spanId) : "",
    attrs,
  };
}

const CSS = String.raw`
*{box-sizing:border-box}:root{--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
body{font:13px/1.5 var(--mono);margin:0;background:#f6f7f9;color:#1a1a1a}
header{background:#1f2937;color:#fff;padding:8px 16px;display:flex;gap:12px;align-items:center}
header h1{font-size:13px;margin:0;font-weight:normal}header b{color:#93c5fd}
header button{font:11px var(--mono);background:#374151;color:#e5e7eb;border:0;border-radius:4px;padding:2px 8px;cursor:pointer}
nav.switch{display:flex;gap:2px;align-items:center}nav.switch span{font-size:10px;color:#9ca3af;margin-right:2px}
nav.switch button.active{background:#2563eb;color:#fff}
.wrap{--tree-width:min(52%,760px);display:flex;height:calc(100vh - 35px)}#view-flame{display:none;height:calc(100vh - 35px);overflow:auto;background:#fff;padding:12px 16px}
.tree{flex:0 0 var(--tree-width);min-width:0;overflow:auto;background:#fff;padding:8px 0}
.splitter{flex:0 0 6px;background:#e5e7eb;cursor:col-resize;touch-action:none;position:relative;outline:0}.splitter::after{content:"";position:absolute;inset:0 -3px}.splitter:hover,.splitter.dragging,.splitter:focus-visible{background:#3b82f6}body.split-resizing{cursor:col-resize;user-select:none}
.pane{flex:1;min-width:0;overflow:auto;padding:16px}.row{white-space:nowrap;cursor:pointer;font-size:12px;padding:2px 8px;border-left:3px solid transparent;display:flex;align-items:baseline;gap:6px;position:relative}.row.agent-row::before{content:"";position:absolute;left:var(--node-indent,4px);top:3px;bottom:3px;width:3px;border-radius:2px;background:var(--node-color,#9ca3af)}
.row:hover{background:#f1f5f9}.row.sel{background:#e0edff;border-left-color:#3b82f6}.row.err{color:#b91c1c}.row.err.sel{background:#fee2e2;border-left-color:#dc2626}
.tw{display:inline-block;width:14px;color:#9ca3af;cursor:pointer;text-align:center;flex:none}.kind{font-size:10px;border-radius:3px;padding:0 5px;flex:none;color:#fff;background:#9ca3af}
.kind.agent,.kind.agent-run{background:#7c3aed}.kind.agent-turn{background:#4f46e5}.kind.framework,.kind.node{background:#2563eb}.kind.model-call{background:#059669}.kind.tool-call{background:#d97706}.kind.action,.kind.operation{background:#0891b2}.kind.service{background:#6b7280}
.dur{color:#6b7280;flex:none}.brief{color:#0d9488;font-size:11px;overflow:hidden;text-overflow:ellipsis}.errdot{color:#dc2626;font-weight:bold;flex:none}
.pane h2{font-size:14px;margin:0 0 4px}.meta{color:#6b7280;font-size:12px;margin-bottom:12px}.findings{margin:0 0 14px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.findings div{font-size:12px;margin:2px 0}.findings .f-error{color:#b91c1c}.findings .f-warn{color:#b45309}.findings .f-info{color:#6b7280}
table.facts{border-collapse:collapse;margin-bottom:14px}table.facts td{border:1px solid #e5e7eb;padding:3px 10px;font-size:12px}table.facts td:first-child{background:#f9fafb;color:#374151}
.feat{margin:0 0 10px;border:1px solid #e5e7eb;border-radius:4px;background:#fff;overflow:hidden}.feat-h{display:flex;justify-content:space-between;align-items:center;padding:5px 10px;font-size:12px;background:#f9fafb;color:#374151;cursor:pointer}.feat-body{margin:0;padding:8px 10px;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow:auto;display:none}.feat.open .feat-body{display:block}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.chip{font-size:11px;border:1px solid #d1d5db;border-radius:4px;padding:2px 8px;cursor:pointer;background:#fff}.chip.sel{background:#e0edff;border-color:#3b82f6}.chip.errc{border-color:#dc2626;color:#b91c1c}.tag{color:#6b7280}
dl.attrs{margin:0}dl.attrs dt{font-size:11px;color:#6b7280;margin-top:10px}dl.attrs dd{margin:2px 0 0;background:#fff;border:1px solid #e5e7eb;border-radius:4px;padding:6px 8px;white-space:pre-wrap;word-break:break-all;font-size:12px;max-height:340px;overflow:auto}dl.attrs dd.json{white-space:normal;word-break:normal;padding:7px 10px}
.renderjson{margin:0;font:12px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word}.renderjson a{text-decoration:none}.renderjson .disclosure{color:#6b7280}.renderjson .syntax{color:#6b7280}.renderjson .key{color:#1d4ed8}.renderjson .string{color:#047857}.renderjson .number{color:#7c3aed}.renderjson .boolean{color:#b45309}.renderjson .keyword{color:#9ca3af}
.faxis{position:relative;height:18px;color:#6b7280;font-size:10px;border-bottom:1px solid #e5e7eb;margin-bottom:6px}.faxis span{position:absolute;transform:translateX(-50%);white-space:nowrap}.flame{position:relative}
.fcell{position:absolute;height:18px;border-radius:2px;font-size:10px;color:#fff;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;padding:1px 4px;cursor:pointer;border:1px solid rgba(255,255,255,.55)}.fcell:hover{filter:brightness(1.18)}.fcell.err{box-shadow:inset 0 0 0 2px #dc2626}
.empty{padding:24px;color:#6b7280;font-size:12px}
`;

// The report must remain a single offline HTML file, so embed the dependency's
// browser source at build time instead of loading it from a CDN at view time.
const SCRIPT = `${renderJsonSource}\n${String.raw`
const TREES=__TREES__,SPANS=__SPANS__,KCOLOR={agent:'#7c3aed','agent-run':'#7c3aed','agent-turn':'#4f46e5',framework:'#2563eb',node:'#2563eb','model-call':'#059669','tool-call':'#d97706',action:'#0891b2',operation:'#0891b2',service:'#6b7280'};
const stackEl=document.getElementById('view-stack'),treeEl=document.getElementById('tree'),splitterEl=document.getElementById('splitter'),paneEl=document.getElementById('pane');
let perspective='full',layout='tree',tree=TREES.full,selectedId=location.hash.slice(1);
let byId={},parentOf={},boxOf={},twOf={},flameBuilt=false,stackMaxDuration=1;
renderjson.set_icons('▸','▾').set_show_to_level(1);
function esc(s){return String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}
function fmtMs(ms){if(ms<1)return(ms*1000).toFixed(0)+'µs';if(ms<1000)return ms.toFixed(0)+'ms';const s=ms/1000;return s<60?s.toFixed(2)+'s':Math.floor(s/60)+'m'+(s%60).toFixed(1)+'s';}
function nameLength(value){return Array.from(String(value||'')).reduce((length,character)=>length+(character.codePointAt(0)>255?2:1),0);}
function fitName(value,budget){const chars=Array.from(String(value||'')),limit=Math.max(0,Math.floor(budget));if(nameLength(value)<=limit)return chars.join('');const ellipsis='…',ellipsisLength=nameLength(ellipsis);if(limit<ellipsisLength)return'';let text='',used=ellipsisLength;for(const character of chars){const width=nameLength(character);if(used+width>limit)break;text+=character;used+=width;}return text+ellipsis;}
function nameCandidates(n){return Array.isArray(n.name_variants)&&n.name_variants.length?n.name_variants.map(String):[String(n.name||'')];}
function compactName(n,expect){const candidates=nameCandidates(n),rawLength=Math.max(1,nameLength(candidates[0])),target=Math.max(0,Math.min(1,expect));for(const candidate of candidates){const actual=nameLength(candidate)/rawLength;if(actual<=target)return[candidate,actual];}const shortest=candidates.reduce((value,candidate)=>nameLength(candidate)<nameLength(value)?candidate:value),fitted=fitName(shortest,Math.floor(rawLength*target));return[fitted,nameLength(fitted)/rawLength];}
function nameForBudget(n,budget){const rawLength=Math.max(1,nameLength(nameCandidates(n)[0]));return compactName(n,budget/rawLength)[0];}
// 父节点 duration 通常包含整棵子树；只有 leaf 参与高度映射，避免同一耗时被父子重复表达。
function maxLeafDuration(ns){let max=1;for(const n of ns){max=Math.max(max,n.children.length?maxLeafDuration(n.children):n.duration_ms||0);}return max;}
function timeHeight(ms,maxMs){const ratio=Math.sqrt(Math.max(0,ms||0)/Math.max(1,maxMs));const base=22,max=base*4;return Math.round(base+(max-base)*ratio);}
// 高度决定可用行数，实际栏宽与缩进深度决定每行字符预算。
function nodeNameLayout(depth,rowHeight){const treeWidth=treeEl.clientWidth||Math.min(760,window.innerWidth*.52),width=Math.max(84,treeWidth-depth*16-210),lines=perspective==='agent'?Math.max(1,Math.min(4,Math.floor(rowHeight/22))):1;return{width,lines,budget:Math.max(12,Math.floor(width/7))*lines};}
// 字符预算只负责第一次估算；真实 DOM 仍溢出时，用本次 actual 继续收紧 expect。
function applyNameLayout(row,n,depth,rowHeight){const name=row.querySelector('.node-name'),nameLayout=nodeNameLayout(depth,rowHeight);if(!name)return;name.style.maxWidth=nameLayout.width+'px';name.style.maxHeight=(nameLayout.lines*18)+'px';name.style.lineHeight='18px';name.style.whiteSpace=nameLayout.lines>1?'normal':'nowrap';name.style.overflowWrap=nameLayout.lines>1?'anywhere':'normal';name.style.overflow='hidden';const rawLength=Math.max(1,nameLength(nameCandidates(n)[0]));let expect=Math.min(1,nameLayout.budget/rawLength);for(let attempt=0;attempt<=nameCandidates(n).length;attempt++){const[value,actual]=compactName(n,expect);name.textContent=value;if(!name.isConnected||name.clientWidth===0||name.scrollWidth<=name.clientWidth+1&&name.scrollHeight<=name.clientHeight+1)break;expect=Math.max(0,Math.min(expect-.01,actual-1/rawLength));}}
function refreshTreeNames(){treeEl.querySelectorAll('.row[data-id]').forEach(row=>{const n=byId[row.dataset.id];if(n)applyNameLayout(row,n,Number(row.dataset.depth),Number(row.dataset.rowHeight));});}
function renderInto(n,depth,parent){byId[n.node_id]=n;parentOf[n.node_id]=parent&&parent.node_id;const box=document.createElement('div'),row=document.createElement('div');row.className='row'+(n.has_error?' err':'');row.dataset.id=n.node_id;row.dataset.depth=String(depth);row.style.paddingLeft=(depth*16+8)+'px';const timedLeaf=perspective==='agent'&&!n.children.length,rowHeight=timedLeaf?timeHeight(n.duration_ms,stackMaxDuration):22;row.dataset.rowHeight=String(rowHeight);
// 竖条跟随 node 的缩进而非整行边框，避免选中态覆盖，也让 leaf 的时间高度直接可见。
if(perspective==='agent'){row.classList.add('agent-row');row.style.setProperty('--node-color',KCOLOR[n.kind]||'#9ca3af');row.style.setProperty('--node-indent',(depth*16+4)+'px');row.style.minHeight=rowHeight+'px';row.style.alignItems='center';}
const tw=document.createElement('span');tw.className='tw';tw.textContent=n.children.length?'▾':'·';row.appendChild(tw);if(n.kind){const k=document.createElement('span');k.className='kind '+n.kind;k.textContent=n.kind;row.appendChild(k);}const nm=document.createElement('span');nm.className='node-name';row.appendChild(nm);applyNameLayout(row,n,depth,rowHeight);const dur=document.createElement('span');dur.className='dur';dur.textContent=fmtMs(n.duration_ms);row.appendChild(dur);if(n.brief){const b=document.createElement('span');b.className='brief';b.textContent='('+n.brief+')';row.appendChild(b);}if(n.has_error){const e=document.createElement('span');e.className='errdot';e.textContent='[ERROR]';row.appendChild(e);}box.appendChild(row);const kids=document.createElement('div');box.appendChild(kids);boxOf[n.node_id]=kids;twOf[n.node_id]=tw;n.children.forEach(c=>kids.appendChild(renderInto(c,depth+1,n)));if((n.folded||n.collapsed)&&n.children.length){kids.style.display='none';tw.textContent='▸';}tw.onclick=ev=>{ev.stopPropagation();const open=kids.style.display!=='none';kids.style.display=open?'none':'';tw.textContent=n.children.length?(open?'▸':'▾'):'·';};row.onclick=()=>select(n.node_id);return box;}
function facts(n){const rows=Object.entries(n.facts||{});return rows.length?'<table class="facts">'+rows.map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+esc(v)+'</td></tr>').join('')+'</table>':'';}
function findings(n){const marks={error:'✗',warn:'▲',info:'·'};return(n.findings||[]).length?'<div class="findings">'+n.findings.map(f=>'<div class="f-'+esc(f.severity)+'">'+(marks[f.severity]||'·')+' ['+esc(f.source)+'] '+esc(f.note)+'</div>').join('')+'</div>':'';}
function features(n){const rows=Object.entries(n.features||{});return rows.length?'<div class="meta">特征：</div>'+rows.map(([k,v])=>'<div class="feat"><div class="feat-h">'+esc(k)+'</div><pre class="feat-body">'+esc(v)+'</pre></div>').join(''):'';}
function attrValue(payload){const dd=document.createElement('dd');if(payload&&payload.kind==='json'){dd.className='json';dd.appendChild(renderjson(payload.value));}else{dd.textContent=payload&&payload.kind==='text'?payload.value:String(payload??'');}return dd;}
function unfold(id){let p=parentOf[id];while(p){const b=boxOf[p];if(b&&b.style.display==='none'){b.style.display='';twOf[p].textContent='▾';}p=parentOf[p];}}
function select(id){document.querySelectorAll('.row.sel').forEach(r=>r.classList.remove('sel'));unfold(id);const row=document.querySelector('.row[data-id="'+CSS.escape(id)+'"]');if(row){row.classList.add('sel');row.scrollIntoView({block:'nearest'});}const n=byId[id];if(!n)return;selectedId=id;location.hash=id;paneEl.innerHTML='<h2>'+esc(n.name)+'</h2><div class="meta">'+esc(n.kind)+' · '+fmtMs(n.duration_ms)+(n.service?' · '+esc(n.service):'')+(n.has_error?' · <b style="color:#dc2626">ERROR：'+esc(n.error)+'</b>':'')+'</div>'+findings(n)+facts(n)+features(n)+'<div class="meta">溯源 span（'+n.span_ids.length+'）：</div><div class="chips">'+n.span_ids.map(sid=>'<span class="chip'+((n.error_span_ids||[]).includes(sid)?' errc':'')+'" data-sid="'+esc(sid)+'">'+(sid===n.primary_span_id?'primary':'卫星')+' · '+esc((SPANS[sid]||{}).operation||sid)+'</span>').join('')+'</div><div id="attrs"></div>';paneEl.querySelectorAll('.chip').forEach(c=>c.onclick=()=>showSpan(c.dataset.sid));paneEl.querySelectorAll('.feat-h').forEach(h=>h.onclick=()=>h.parentElement.classList.toggle('open'));const sid=(n.error_span_ids||[])[0]||n.primary_span_id;if(sid)showSpan(sid);else document.getElementById('attrs').innerHTML='<div class="meta">视图压缩节点，无独立 span</div>';}
function showSpan(sid){paneEl.querySelectorAll('.chip').forEach(c=>c.classList.toggle('sel',c.dataset.sid===sid));const sp=SPANS[sid],box=document.getElementById('attrs');box.replaceChildren();if(!sp){const missing=document.createElement('div');missing.className='meta';missing.textContent='span 不在快照内';box.appendChild(missing);return;}const meta=document.createElement('div');meta.className='meta';meta.textContent='span '+sid+' · '+sp.operation+' · '+fmtMs(sp.duration_ms);box.appendChild(meta);const attrs=document.createElement('dl');attrs.className='attrs';for(const [key,value] of Object.entries(sp.attrs)){const name=document.createElement('dt');name.textContent=key;attrs.append(name,attrValue(value));}box.appendChild(attrs);}
function firstError(ns){for(const n of ns){if(n.has_error&&n.kind)return n.node_id;const child=firstError(n.children);if(child)return child;}return null;}
function firstReal(ns){for(const n of ns){if(n.kind)return n.node_id;const child=firstReal(n.children);if(child)return child;}return null;}
function renderTree(){tree=TREES[perspective]||{roots:[]};treeEl.replaceChildren();stackMaxDuration=perspective==='agent'?maxLeafDuration(tree.roots):1;byId={};parentOf={};boxOf={};twOf={};flameBuilt=false;tree.roots.forEach(r=>treeEl.appendChild(renderInto(r,0,null)));refreshTreeNames();if(!tree.roots.length){treeEl.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';paneEl.innerHTML='<div class="empty">切换到“完整”查看全部节点</div>';if(layout==='flame')buildFlame();return;}const wanted=selectedId&&byId[selectedId]?selectedId:null;select(wanted||firstError(tree.roots)||firstReal(tree.roots)||tree.roots[0].node_id);if(layout==='flame')buildFlame();}
function splitBounds(){const available=Math.max(0,stackEl.clientWidth-splitterEl.offsetWidth),minTree=Math.min(220,Math.max(120,available*.35)),minPane=Math.min(280,Math.max(160,available*.35));return{min:minTree,max:Math.max(minTree,available-minPane)};}
let pendingTreeWidth=null,splitFrame=0;
function applyTreeWidth(){splitFrame=0;if(pendingTreeWidth===null)return;const bounds=splitBounds(),width=Math.max(bounds.min,Math.min(bounds.max,pendingTreeWidth));pendingTreeWidth=null;stackEl.style.setProperty('--tree-width',width+'px');splitterEl.setAttribute('aria-valuemin',String(Math.round(bounds.min)));splitterEl.setAttribute('aria-valuemax',String(Math.round(bounds.max)));splitterEl.setAttribute('aria-valuenow',String(Math.round(width)));refreshTreeNames();}
// Pointer events may arrive faster than layout can settle; one width/name pass per frame keeps dragging smooth.
function scheduleTreeWidth(width){pendingTreeWidth=width;if(!splitFrame)splitFrame=requestAnimationFrame(applyTreeWidth);}
function finishSplit(event){if(splitterEl.hasPointerCapture(event.pointerId))splitterEl.releasePointerCapture(event.pointerId);splitterEl.classList.remove('dragging');document.body.classList.remove('split-resizing');}
splitterEl.onpointerdown=event=>{if(event.button!==0)return;splitterEl.setPointerCapture(event.pointerId);splitterEl.classList.add('dragging');document.body.classList.add('split-resizing');scheduleTreeWidth(event.clientX-stackEl.getBoundingClientRect().left);};
splitterEl.onpointermove=event=>{if(splitterEl.hasPointerCapture(event.pointerId))scheduleTreeWidth(event.clientX-stackEl.getBoundingClientRect().left);};
splitterEl.onpointerup=finishSplit;splitterEl.onpointercancel=finishSplit;
splitterEl.onkeydown=event=>{if(event.key!=='ArrowLeft'&&event.key!=='ArrowRight')return;event.preventDefault();const direction=event.key==='ArrowLeft'?-1:1;scheduleTreeWidth(treeEl.getBoundingClientRect().width+direction*(event.shiftKey?60:20));};
window.addEventListener('resize',()=>{if(layout==='tree')scheduleTreeWidth(treeEl.getBoundingClientRect().width);});
document.getElementById('expand').onclick=()=>treeEl.querySelectorAll('.tw').forEach(t=>{if(t.textContent==='▸')t.click();});document.getElementById('fold').onclick=()=>treeEl.querySelectorAll('.tw').forEach(t=>{if(t.textContent==='▾')t.click();});
const views={tree:document.getElementById('view-stack'),flame:document.getElementById('view-flame')};
function showLayout(next){layout=next;Object.entries(views).forEach(([key,element])=>element.style.display=key===next?(key==='tree'?'flex':'block'):'none');document.querySelectorAll('[data-layout]').forEach(button=>button.classList.toggle('active',button.dataset.layout===next));document.getElementById('expand').style.display=next==='tree'?'':'none';document.getElementById('fold').style.display=next==='tree'?'':'none';if(next==='tree')requestAnimationFrame(()=>scheduleTreeWidth(treeEl.getBoundingClientRect().width));if(next==='flame'&&!flameBuilt)buildFlame();}
function showPerspective(next){perspective=next;document.querySelectorAll('[data-perspective]').forEach(button=>button.classList.toggle('active',button.dataset.perspective===next));renderTree();}
document.querySelectorAll('[data-layout]').forEach(button=>button.onclick=()=>showLayout(button.dataset.layout));document.querySelectorAll('[data-perspective]').forEach(button=>button.onclick=()=>showPerspective(button.dataset.perspective));
function buildFlame(){const box=document.getElementById('flame'),axis=document.getElementById('faxis');box.replaceChildren();axis.replaceChildren();if(!tree.roots.length){box.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';flameBuilt=true;return;}let t0=Infinity,t1=-Infinity,maxD=0;(function scan(ns,d){ns.forEach(n=>{t0=Math.min(t0,n.start_ms);t1=Math.max(t1,n.start_ms+n.duration_ms);maxD=Math.max(maxD,d);scan(n.children,d+1);});})(tree.roots,0);const span=Math.max(t1-t0,1e-6),rowH=20;box.style.height=((maxD+1)*rowH+4)+'px';for(let i=0;i<=10;i++){const s=document.createElement('span');s.style.left=(i*10)+'%';s.textContent=fmtMs(span*i/10);axis.appendChild(s);}(function place(ns,d){ns.forEach(n=>{const c=document.createElement('div');c.className='fcell'+(n.has_error?' err':'');c.style.left=((n.start_ms-t0)/span*100)+'%';c.style.width=Math.max(n.duration_ms/span*100,.15)+'%';c.style.top=(d*rowH)+'px';c.style.background=KCOLOR[n.kind]||'#9ca3af';box.appendChild(c);c.textContent=nameForBudget(n,Math.max(1,Math.floor(c.clientWidth/7)));c.title=compactName(n,1)[0]+' · '+n.kind+' · '+fmtMs(n.duration_ms)+(n.service?' · '+n.service:'')+(n.has_error?' · ERROR':'');c.onclick=()=>{showLayout('tree');select(n.node_id);};place(n.children,d+1);});})(tree.roots,0);flameBuilt=true;}
renderTree();showLayout('tree');
`}`;

export function renderInteractive(
  context: TraceContext,
  findings: Record<string, Finding[]> = {},
  options: {
    featureRegistry?: FeatureRegistry;
    facetRegistry?: FacetRegistry;
    agentRunIR?: AgentRunIR;
  } = {},
): string {
  const featureRegistry = options.featureRegistry ?? new FeatureRegistry(builtinFeatures());
  const facetRegistry = options.facetRegistry ?? new FacetRegistry(builtinFacets());
  const byId = new Map(context.nodes.map((node) => [node.node_id, node]));
  const trees: Record<string, { roots: Array<Record<string, unknown>> }> = {
    full: {
      roots: renderDisplay(context.view(), findings, facetRegistry, { perspective: "full" })
        .map((root) => displayPayload(context, root, byId, featureRegistry)),
    },
  };
  if (options.agentRunIR?.runs.length) {
    trees.agent = { roots: agentRunRoots(context, options.agentRunIR, findings) };
  }
  const referenced = new Set(context.nodes.flatMap((node) => node.span_ids));
  const spans = Object.fromEntries(
    [...referenced].filter((spanId) => context.spans.has(spanId)).map((spanId) => [spanId, spanPayload(context, spanId)]),
  );
  const embed = (value: unknown) => JSON.stringify(value).replaceAll("</", "<\\/");
  const script = SCRIPT.replace("__TREES__", embed(trees)).replace("__SPANS__", embed(spans));
  const title = htmlEscape(context.trace_id);
  const errorCount = context.nodes.filter((node) => node.has_error).length;
  const agentButton = trees.agent ? '<button data-perspective="agent">Agent</button>' : "";
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>trace ${title}</title><style>${CSS}</style></head>
<body><header><h1>trace <b>${title}</b> · ${context.nodes.length} nodes / ${context.spans.size} spans · errors ${errorCount}</h1>
<nav class="switch"><span>侧重点</span><button data-perspective="full" class="active">完整</button>${agentButton}</nav>
<nav class="switch"><span>形态</span><button data-layout="tree" class="active">调用栈</button><button data-layout="flame">火焰图</button></nav><button id="expand">全部展开</button><button id="fold">全部折叠</button></header>
<div class="wrap" id="view-stack"><div class="tree" id="tree"></div><div class="splitter" id="splitter" role="separator" aria-label="调整调用栈与节点详情宽度" aria-orientation="vertical" tabindex="0"></div><div class="pane" id="pane"></div></div>
<div id="view-flame"><div class="faxis" id="faxis"></div><div class="flame" id="flame"></div></div><script>${script}</script></body></html>\n`;
}
