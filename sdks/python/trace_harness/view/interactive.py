"""交互式 trace 页面——菜单 + 功能区，单文件零网络，trace 深看的人用视图。

页面有两个正交维度：
- perspective：完整 Node Tree / 业务提取的 AgentRun IR，决定看什么；
- layout：调用栈 / 火焰图，决定怎么画。调用栈是左树右详情，火焰图是时间轴 icicle。

HTML 只此一个交互页：非交互的 HTML 无保留价值（要静态/可粘贴版用 md），故旧的
view/html.py（report_kit 静态表格）已删。**刻意不走 report_kit**——交互能力超出中立文档 IR
的表达范围，硬塞会把 report_kit 变成 UI 框架。

数据面：
- 左树 = **`engine.render()` 的 DisplayNode 树**（facet + perspective 投影后）；
  brief / findings 由 facet+engine 算好，folded 合成行（`内部调用 ×N` / `turn ×N`）**默认收起、
  点开展开成员**（虚拟节点可下钻；`folded>0` 由 payload 透出给前端）
- timing / 原文 / facts 不在 DisplayNode 上 → 用其 `node_ids` 回查 ctx（duration 优先 `wall_ms`）
- 右详情 = node.facts 表 + 溯源 span chips（primary/卫星/错误标注）+ attrs 下钻
- attrs 超长截断并指向 dump-io（token 漏斗：页面是中分辨率视图，全文留给专项下钻）
- Finding 内联在 node payload（engine 已绑/上浮），右栏判读区展示
"""

from __future__ import annotations

import html as html_mod
import json

from trace_harness.feature import lazy_features
from trace_harness.feature.builtins import BUILTIN_FEATURES
from trace_harness.feature.registry import FeatureRegistry
from trace_harness.model.agent import AgentRunIR
from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node
from trace_harness.view.agent_run import agent_run_roots
from trace_harness.view.display import Compact, DisplayName, DisplayNode, name_projections
from trace_harness.view.engine import render as _engine_render
from trace_harness.view.facet import RenderConfig
from trace_harness.view.facets import builtin_facets
from trace_harness.view.registry import FacetRegistry
from trace_harness.view.tool_name import tool_name_detail

ATTR_TRUNCATE = 4000  # 单 attr 值超过即截断；完整原文按 span_id 走 dump-io


def _disp_payload(
    ctx: TraceContext,
    d: DisplayNode,
    byid: dict[str, Node],
    feature_registry: FeatureRegistry,
) -> dict:
    """DisplayNode（facet 折叠后的显示树）→ 交互页 payload。timing/原文/facts 用 `node_ids`
    回查底层 Node（duration 优先 wall_ms = 含子孙的 wall-clock duration）；折叠/聚合合成行（kind 空）无底层 node，
    时间用所聚合 node 的包络。findings 由 engine 绑在 DisplayNode 上（折叠则已上浮）。"""
    brief = "  ".join(f"{f.label}={f.value}" for f in d.brief)
    fnd = [{"severity": f.severity, "source": f.source, "note": f.note} for f in d.findings]
    kids = [_disp_payload(ctx, c, byid, feature_registry) for c in d.children]
    node = byid.get(d.node_ids[0]) if (d.kind and d.node_ids) else None
    if node is not None:
        display_name = str(node.facts.get("tool") or d.name) if node.kind == "tool-call" else d.name
        arguments = (
            ctx.raw_attr(str(node.facts.get("io_span") or node.primary_span_id)).get(
                "gen_ai.tool.call.arguments"
            )
            if node.kind == "tool-call"
            else None
        )
        compact = (
            d
            if isinstance(d, Compact)
            else (
                DisplayName(display_name, tool_name_detail(arguments))
                if node.kind == "tool-call"
                else d
            )
        )
        return {
            "node_id": node.node_id,
            "kind": d.kind,
            "name": d.name,
            "name_variants": name_projections(compact, d.name),
            "service": node.service,
            "start_ms": node.start_ms,
            "duration_ms": node.facts.get("wall_ms", node.duration_ms),
            "has_error": node.has_error,
            "error": ctx.error_text(node.error_anchor) if node.has_error else "",
            "brief": brief,
            "findings": fnd,
            "facts": {k: str(v) for k, v in (node.facts or {}).items() if not k.startswith("_")},
            # lazy Feature（curl/bash…）：渲染期算好嵌入（静态页无后端，按需算不了）；非适用节点为空
            "features": {
                k: v
                for k, v in lazy_features(
                    node,
                    ctx.view(),
                    ctx.raw_attr,
                    registry=feature_registry,
                ).items()
                if v
            },
            "span_ids": node.span_ids,
            "primary_span_id": node.primary_span_id,
            "error_span_ids": node.error_span_ids,
            "folded": d.folded,  # >0 = 折叠合成节点，前端默认收起（点开才展）
            "children": kids,
        }
    # 合成行（折叠/聚合摘要，kind 空、无单一底层 node）：时间取所聚合 node 的包络
    folded = [byid[nid] for nid in d.node_ids if nid in byid]
    start = min((n.start_ms for n in folded), default=0.0)
    end = max((n.end_ms for n in folded), default=0.0)
    return {
        "node_id": "fold:" + "·".join(d.node_ids[:3]) if d.node_ids else "fold:" + d.name,
        "kind": "",
        "name": d.name,
        "name_variants": name_projections(d),
        "service": "",
        "start_ms": start,
        "duration_ms": end - start,
        "has_error": False,
        "error": "",
        "brief": brief,
        "findings": fnd,
        "facts": {},
        "features": {},
        "span_ids": [],
        "primary_span_id": "",
        "error_span_ids": [],
        "folded": d.folded,  # >0 = 折叠合成节点，前端默认收起
        "children": kids,
    }


def _span_payload(ctx: TraceContext, sid: str) -> dict:
    s = ctx.spans[sid]
    attrs = {}
    for k, v in (s.attrs or {}).items():
        text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        if len(text) > ATTR_TRUNCATE:
            text = (
                text[:ATTR_TRUNCATE]
                + f"\n…[已截断，共 {len(text)} 字符；完整原文：dump-io --span {sid}]"
            )
        attrs[k] = text
    return {
        "service": s.service,
        "operation": s.name,
        "duration_ms": s.dur_ms,
        "has_error": s.has_error,
        "error": ctx.error_text(sid) if s.has_error else "",
        "attrs": attrs,
    }


_CSS = """
*{box-sizing:border-box}
:root{--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
body{font:13px/1.5 var(--mono);margin:0;background:#f6f7f9;color:#1a1a1a}
header{background:#1f2937;color:#fff;padding:8px 16px;display:flex;gap:12px;align-items:center}
header h1{font-size:13px;margin:0;font-weight:normal}
header b{color:#93c5fd}
header button{font:11px var(--mono);background:#374151;color:#e5e7eb;border:0;border-radius:4px;padding:2px 8px;cursor:pointer}
nav.switch{display:flex;gap:2px;align-items:center}
nav.switch span{font-size:10px;color:#9ca3af;margin-right:2px}
nav.switch button.active{background:#2563eb;color:#fff}
.wrap{display:flex;height:calc(100vh - 35px)}
#view-flame{display:none;height:calc(100vh - 35px);overflow:auto;background:#fff;padding:12px 16px}
.faxis{position:relative;height:18px;color:#6b7280;font-size:10px;border-bottom:1px solid #e5e7eb;margin-bottom:6px}
.faxis span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.flame{position:relative}
.fcell{position:absolute;height:18px;border-radius:2px;font-size:10px;color:#fff;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis;padding:1px 4px;cursor:pointer;border:1px solid rgba(255,255,255,.55)}
.fcell:hover{filter:brightness(1.18)}
.fcell.err{box-shadow:inset 0 0 0 2px #dc2626}
.tree{flex:0 0 52%;max-width:760px;overflow:auto;border-right:1px solid #e5e7eb;background:#fff;padding:8px 0}
.pane{flex:1;overflow:auto;padding:16px}
.row{white-space:nowrap;cursor:pointer;font-size:12px;padding:2px 8px;border-left:3px solid transparent;display:flex;align-items:baseline;gap:6px;position:relative}
.row.agent-row::before{content:"";position:absolute;left:var(--node-indent,4px);top:3px;bottom:3px;width:3px;border-radius:2px;background:var(--node-color,#9ca3af)}
.row:hover{background:#f1f5f9}
.row.sel{background:#e0edff;border-left-color:#3b82f6}
.row.err{color:#b91c1c}
.row.err.sel{background:#fee2e2;border-left-color:#dc2626}
.tw{display:inline-block;width:14px;color:#9ca3af;cursor:pointer;text-align:center;flex:none}
.kind{font-size:10px;border-radius:3px;padding:0 5px;flex:none;color:#fff;background:#9ca3af}
.kind.agent{background:#7c3aed}.kind.framework{background:#2563eb}.kind.model-call{background:#059669}
.kind.tool-call{background:#d97706}.kind.action,.kind.operation{background:#0891b2}.kind.node{background:#2563eb}
.kind.agent-run{background:#7c3aed}.kind.agent-turn{background:#4f46e5}
.kind.service{background:#6b7280}
.dur{color:#6b7280;flex:none}
.brief{color:#0d9488;font-size:11px;overflow:hidden;text-overflow:ellipsis}
.errdot{color:#dc2626;font-weight:bold;flex:none}
.pane h2{font-size:14px;margin:0 0 4px}
.meta{color:#6b7280;font-size:12px;margin-bottom:12px}
.findings{margin:0 0 14px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.findings div{font-size:12px;margin:2px 0}
.findings .f-error{color:#b91c1c}.findings .f-warn{color:#b45309}.findings .f-info{color:#6b7280}
table.facts{border-collapse:collapse;margin-bottom:14px}
table.facts td{border:1px solid #e5e7eb;padding:3px 10px;font-size:12px}
table.facts td:first-child{background:#f9fafb;color:#374151}
.feat{margin:0 0 10px;border:1px solid #e5e7eb;border-radius:4px;background:#fff;overflow:hidden}
.feat-h{display:flex;justify-content:space-between;align-items:center;padding:5px 10px;font-size:12px;background:#f9fafb;color:#374151;cursor:pointer}
.feat-t::before{content:'▸ ';color:#9ca3af}
.feat.open .feat-t::before{content:'▾ '}
.feat-body{margin:0;padding:8px 10px;font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:300px;overflow:auto;display:none}
.feat.open .feat-body{display:block}
.copy{font-size:11px;padding:1px 8px;border:1px solid #d1d5db;border-radius:3px;background:#fff;cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.chip{font-size:11px;border:1px solid #d1d5db;border-radius:4px;padding:2px 8px;cursor:pointer;background:#fff}
.chip.sel{background:#e0edff;border-color:#3b82f6}
.chip.errc{border-color:#dc2626;color:#b91c1c}
.tag{color:#6b7280}
dl.attrs{margin:0}
dl.attrs dt{font-size:11px;color:#6b7280;margin-top:10px}
dl.attrs dd{margin:2px 0 0;background:#fff;border:1px solid #e5e7eb;border-radius:4px;
  padding:6px 8px;white-space:pre-wrap;word-break:break-all;font-size:12px;max-height:340px;overflow:auto}
.empty{padding:24px;color:#6b7280;font-size:12px}
"""

_JS = """
const TREES=__TREES__,SPANS=__SPANS__;
const SEV={error:'✗',warn:'▲',info:'·'};
const KCOLOR={agent:'#7c3aed','agent-run':'#7c3aed','agent-turn':'#4f46e5',framework:'#2563eb',
  node:'#2563eb','model-call':'#059669','tool-call':'#d97706',action:'#0891b2',operation:'#0891b2',service:'#6b7280'};
const treeEl=document.getElementById('tree'),paneEl=document.getElementById('pane');
let perspective='full',layout='tree',tree=TREES.full,selectedId=location.hash.slice(1);
let byId={},parentOf={},boxOf={},twOf={},flameBuilt=false,stackMaxDuration=1;
function fmtMs(ms){if(ms<1)return (ms*1000).toFixed(0)+'µs';if(ms<1000)return ms.toFixed(0)+'ms';
  const s=ms/1000;if(s<60)return s.toFixed(2)+'s';return Math.floor(s/60)+'m'+(s%60).toFixed(1)+'s';}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function kindClass(k){return k.replace(/[^a-zA-Z0-9-]/g,'-');}
function nameLength(value){return Array.from(String(value||'')).length;}
function fitName(value,budget){const chars=Array.from(String(value||'')),limit=Math.max(0,Math.floor(budget));
  if(chars.length<=limit)return chars.join('');if(limit===0)return'';if(limit===1)return'…';
  return chars.slice(0,limit-1).join('')+'…';}
// 展示节点自己提供从高到低保真度的语义表示；渲染器只负责按预算选择。
function compactName(n,budget){const candidates=Array.isArray(n.name_variants)&&n.name_variants.length
    ?n.name_variants.map(String):[String(n.name||'')];
  const limit=Math.max(0,Math.floor(budget));
  for(const candidate of candidates){if(nameLength(candidate)<=limit)return candidate;}
  return fitName(candidates[candidates.length-1],limit);}
// 父节点 duration 通常包含整棵子树；只有 leaf 参与高度映射，避免同一耗时被父子重复表达。
function maxLeafDuration(ns){let max=1;for(const n of ns){max=Math.max(max,n.children.length
    ?maxLeafDuration(n.children):n.duration_ms||0);}return max;}
function timeHeight(ms,maxMs){const ratio=Math.sqrt(Math.max(0,ms||0)/Math.max(1,maxMs));
  const base=22,max=base*4;return Math.round(base+(max-base)*ratio);}
// 高度决定可用行数，实际栏宽与缩进深度决定每行字符预算。
function agentNameLayout(depth,rowHeight){const treeWidth=treeEl.clientWidth||Math.min(760,window.innerWidth*.52);
  const width=Math.max(84,treeWidth-depth*16-210),lines=Math.max(1,Math.min(4,Math.floor(rowHeight/22)));
  return{width,lines,budget:Math.max(12,Math.floor(width/7))*lines};}
function renderInto(n,depth,parent){const box=document.createElement('div');
  renderRowInto(box,n,depth,parent);return box;}
function renderRowInto(box,n,depth,parent){
  byId[n.node_id]=n;parentOf[n.node_id]=parent?parent.node_id:null;
  const row=document.createElement('div');
  row.className='row'+(n.has_error?' err':'');row.dataset.id=n.node_id;
  row.style.paddingLeft=(depth*16+8)+'px';
  const timedLeaf=perspective==='agent'&&!n.children.length;
  const rowHeight=timedLeaf?timeHeight(n.duration_ms,stackMaxDuration):22;
  const nameLayout=perspective==='agent'?agentNameLayout(depth,rowHeight):null;
  // 竖条跟随 node 的缩进而非整行边框，避免选中态覆盖，也让 leaf 的时间高度直接可见。
  if(nameLayout){row.classList.add('agent-row');row.style.setProperty('--node-color',KCOLOR[n.kind]||'#9ca3af');
    row.style.setProperty('--node-indent',(depth*16+4)+'px');
    row.style.minHeight=rowHeight+'px';row.style.alignItems='center';}
  const tw=document.createElement('span');tw.className='tw';
  tw.textContent=n.children.length?'▾':'·';row.appendChild(tw);
  if(n.kind){const k=document.createElement('span');k.className='kind '+kindClass(n.kind);k.textContent=n.kind;row.appendChild(k);}
  const nm=document.createElement('span');nm.textContent=compactName(n,nameLayout?nameLayout.budget:48);
  if(nameLayout){nm.style.maxWidth=nameLayout.width+'px';nm.style.maxHeight=(nameLayout.lines*18)+'px';
    nm.style.lineHeight='18px';nm.style.whiteSpace='normal';nm.style.overflowWrap='anywhere';nm.style.overflow='hidden';}
  row.appendChild(nm);
  const d=document.createElement('span');d.className='dur';d.textContent=fmtMs(n.duration_ms);row.appendChild(d);
  if(n.brief){const be=document.createElement('span');be.className='brief';be.textContent='('+n.brief+')';row.appendChild(be);}
  if(n.has_error){const e=document.createElement('span');e.className='errdot';e.textContent='[ERROR]';row.appendChild(e);}
  box.appendChild(row);
  const kidsBox=document.createElement('div');box.appendChild(kidsBox);
  boxOf[n.node_id]=kidsBox;twOf[n.node_id]=tw;
  n.children.forEach(c=>kidsBox.appendChild(renderInto(c,depth+1,n)));
  if((n.folded||n.collapsed)&&n.children.length){kidsBox.style.display='none';tw.textContent='▸';}  // 聚合节点和低关注 operation 默认收起
  tw.addEventListener('click',ev=>{ev.stopPropagation();
    const open=kidsBox.style.display!=='none';
    kidsBox.style.display=open?'none':'';tw.textContent=n.children.length?(open?'▸':'▾'):'·';});
  row.addEventListener('click',()=>select(n.node_id));
  return row;}
function factsTable(n){const rows=Object.entries(n.facts||{});
  if(!rows.length)return'';
  return '<table class="facts">'+rows.map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+esc(v)+'</td></tr>').join('')+'</table>';}
function featuresBlock(n){const fs=Object.entries(n.features||{});
  if(!fs.length)return'';
  return '<div class="meta">特征：</div>'+fs.map(([k,v])=>
    '<div class="feat"><div class="feat-h"><span class="feat-t">'+esc(k)+'</span>'
    +'<button class="copy">copy</button></div><pre class="feat-body">'+esc(v)+'</pre></div>').join('');}
function findingsBlock(n){const fs=n.findings||[];
  if(!fs.length)return'';
  return '<div class="findings">'+fs.map(f=>'<div class="f-'+esc(f.severity)+'">'
    +(SEV[f.severity]||'·')+' ['+esc(f.source)+'] '+esc(f.note||'')+'</div>').join('')+'</div>';}
function spanChip(sid,n){const sp=SPANS[sid]||{};const isPrimary=sid===n.primary_span_id;
  const isErr=(n.error_span_ids||[]).includes(sid);
  const tag=isPrimary?'primary':'卫星';
  return '<span class="chip'+(isErr?' errc':'')+'" data-sid="'+esc(sid)+'">'
    +esc(tag)+' · '+esc(sp.operation||sid)+' <span class="tag">('+esc(sp.service||'?')+')</span></span>';}
function unfoldAncestors(id){let p=parentOf[id];
  while(p){const kb=boxOf[p];
    if(kb&&kb.style.display==='none'){kb.style.display='';const t=twOf[p];if(t)t.textContent='▾';}
    p=parentOf[p];}}
function select(id){
  document.querySelectorAll('.row.sel').forEach(r=>r.classList.remove('sel'));
  unfoldAncestors(id);
  const row=document.querySelector('.row[data-id="'+CSS.escape(id)+'"]');
  if(row){row.classList.add('sel');row.scrollIntoView({block:'nearest'});}
  const n=byId[id];if(!n)return;
  selectedId=id;location.hash=id;
  let h='<h2>'+esc(n.name)+'</h2>'
    +'<div class="meta">'+esc(n.kind)+' · '+fmtMs(n.duration_ms)
    +(n.service?' · '+esc(n.service):'')
    +(n.has_error?' · <b style="color:#dc2626">ERROR'+(n.error?'：'+esc(n.error):'')+'</b>':'')
    +'</div>'+findingsBlock(n)+factsTable(n)+featuresBlock(n)
    +'<div class="meta">溯源 span（'+n.span_ids.length+'）：</div>'
    +'<div class="chips">'+n.span_ids.map(sid=>spanChip(sid,n)).join('')+'</div>'
    +'<div id="attrs"></div>';
  paneEl.innerHTML=h;
  paneEl.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>showSpan(c.dataset.sid)));
  paneEl.querySelectorAll('.feat-h').forEach(h=>h.addEventListener('click',e=>{
    if(e.target.closest('.copy'))return;  // copy 不触发折叠
    h.parentElement.classList.toggle('open');}));
  paneEl.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',e=>{
    e.stopPropagation();
    const body=b.closest('.feat').querySelector('.feat-body');
    if(body){navigator.clipboard.writeText(body.textContent);b.textContent='copied';
      setTimeout(()=>{b.textContent='copy';},1000);}}));
  const firstSid=(n.error_span_ids&&n.error_span_ids[0])||n.primary_span_id;
  if(firstSid)showSpan(firstSid);
  else document.getElementById('attrs').innerHTML='<div class="meta">视图压缩节点，无独立 span</div>';}
function showSpan(sid){
  paneEl.querySelectorAll('.chip').forEach(c=>c.classList.toggle('sel',c.dataset.sid===sid));
  const sp=SPANS[sid];const box=document.getElementById('attrs');
  if(!sp){box.innerHTML='<div class="meta">span '+esc(sid)+' 不在快照内</div>';return;}
  box.innerHTML='<div class="meta">span '+esc(sid)+' · '+esc(sp.operation||'')+' · '+fmtMs(sp.duration_ms)
    +(sp.has_error&&sp.error?' · <b style="color:#dc2626">'+esc(sp.error)+'</b>':'')+'</div>'
    +'<dl class="attrs">'+Object.entries(sp.attrs).map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>';}
document.getElementById('expand').addEventListener('click',()=>{
  treeEl.querySelectorAll('.tw').forEach(t=>{if(t.textContent==='▸')t.click();});});
document.getElementById('fold').addEventListener('click',()=>{
  treeEl.querySelectorAll('.tw').forEach(t=>{if(t.textContent==='▾')t.click();});});
// Perspective（看什么）与 Layout（怎么画）正交。
const views={tree:document.getElementById('view-stack'),flame:document.getElementById('view-flame')};
function showLayout(next){
  layout=next;
  // 显式 flex/block：style.display='' 会回落到样式表里 #view-flame 的 display:none
  Object.entries(views).forEach(([k,el])=>{el.style.display=(k===next)?(k==='tree'?'flex':'block'):'none';});
  document.querySelectorAll('[data-layout]').forEach(b=>b.classList.toggle('active',b.dataset.layout===next));
  // 展开/折叠只对调用栈有意义
  document.getElementById('expand').style.display=(next==='tree')?'':'none';
  document.getElementById('fold').style.display=(next==='tree')?'':'none';
  if(next==='flame'&&!flameBuilt)buildFlame();}
function showPerspective(next){
  perspective=next;
  document.querySelectorAll('[data-perspective]').forEach(b=>b.classList.toggle('active',b.dataset.perspective===next));
  renderTree();}
document.querySelectorAll('[data-layout]').forEach(b=>b.addEventListener('click',()=>showLayout(b.dataset.layout)));
document.querySelectorAll('[data-perspective]').forEach(b=>b.addEventListener('click',()=>showPerspective(b.dataset.perspective)));
function firstError(ns){for(const n of ns){if(n.has_error&&n.kind)return n.node_id;
  const k=firstError(n.children);if(k)return k;}return null;}
function firstReal(ns){for(const n of ns){if(n.kind)return n.node_id;
  const k=firstReal(n.children);if(k)return k;}return null;}
function renderTree(){
  tree=TREES[perspective]||{roots:[]};treeEl.replaceChildren();
  stackMaxDuration=perspective==='agent'?maxLeafDuration(tree.roots):1;
  byId={};parentOf={};boxOf={};twOf={};flameBuilt=false;
  tree.roots.forEach(r=>treeEl.appendChild(renderInto(r,0,null)));
  if(!tree.roots.length){treeEl.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';
    paneEl.innerHTML='<div class="empty">切换到“完整”查看全部节点</div>';
    if(layout==='flame')buildFlame();return;}
  const wanted=selectedId&&byId[selectedId]?selectedId:null;
  select(wanted||firstError(tree.roots)||firstReal(tree.roots)||tree.roots[0].node_id);
  if(layout==='flame')buildFlame();}
// 火焰图（icicle）：x=wall-clock time，行=深度，色=kind；按当前 perspective 构建
function buildFlame(){
  const box=document.getElementById('flame'),axis=document.getElementById('faxis');
  box.replaceChildren();axis.replaceChildren();
  if(!tree.roots.length){box.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';
    flameBuilt=true;return;}
  let t0=Infinity,t1=-Infinity,maxD=0;
  (function scan(ns,d){ns.forEach(n=>{t0=Math.min(t0,n.start_ms);
    t1=Math.max(t1,n.start_ms+n.duration_ms);maxD=Math.max(maxD,d);scan(n.children,d+1);});})(tree.roots,0);
  const span=Math.max(t1-t0,1e-6),rowH=20;
  box.style.height=((maxD+1)*rowH+4)+'px';
  for(let i=0;i<=10;i++){const s=document.createElement('span');
    s.style.left=(i*10)+'%';s.textContent=fmtMs(span*i/10);axis.appendChild(s);}
  (function place(ns,d){ns.forEach(n=>{
    const c=document.createElement('div');
    c.className='fcell'+(n.has_error?' err':'');
    c.style.left=((n.start_ms-t0)/span*100)+'%';
    c.style.width=Math.max(n.duration_ms/span*100,0.15)+'%';
    c.style.top=(d*rowH)+'px';
    c.style.background=KCOLOR[n.kind]||'#9ca3af';
    box.appendChild(c);
    c.textContent=compactName(n,Math.max(1,Math.floor(c.clientWidth/7)));
    c.title=compactName(n,Number.MAX_SAFE_INTEGER)+' · '+n.kind+' · '+fmtMs(n.duration_ms)
      +(n.service?' · '+n.service:'')+(n.has_error?' · ERROR':'');
    c.addEventListener('click',()=>{showLayout('tree');select(n.node_id);});
    place(n.children,d+1);});})(tree.roots,0);flameBuilt=true;}
renderTree();showLayout('tree');
"""


def render_interactive(
    ctx: TraceContext,
    findings: dict[str, list[Finding]] | None = None,
    *,
    facet_registry: FacetRegistry | None = None,
    feature_registry: FeatureRegistry | None = None,
    agent_run_ir: AgentRunIR | None = None,
) -> str:
    """渲染为单文件交互 HTML（菜单：调用栈=左树右详情 / 火焰图）。findings 为 diagnose 输出，可省。"""
    findings = findings or {}
    facet_registry = facet_registry or FacetRegistry(builtin_facets())
    feature_registry = feature_registry or FeatureRegistry(BUILTIN_FEATURES)
    byid = {n.node_id: n for n in ctx.nodes}
    roots = _engine_render(
        ctx.view(),
        findings,
        registry=facet_registry,
        config=RenderConfig(perspective="full"),
    )
    trees_payload = {
        "full": {"roots": [_disp_payload(ctx, d, byid, feature_registry) for d in roots]}
    }
    if agent_run_ir is not None and agent_run_ir.runs:
        trees_payload["agent"] = {"roots": agent_run_roots(ctx, agent_run_ir, findings)}
    referenced = {sid for n in ctx.nodes for sid in n.span_ids}
    spans_payload = {sid: _span_payload(ctx, sid) for sid in referenced if sid in ctx.spans}

    # 嵌入 <script> 的 JSON 必须转义 "</"，防止 "</script>" 提前闭合标签
    def _embed(obj: object) -> str:
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    js = _JS.replace("__TREES__", _embed(trees_payload)).replace("__SPANS__", _embed(spans_payload))
    title = html_mod.escape(ctx.trace_id)
    n_err = sum(1 for n in ctx.nodes if n.has_error)
    agent_button = (
        '<button data-perspective="agent">Agent</button>' if "agent" in trees_payload else ""
    )
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>trace {title}</title>
<style>{_CSS}</style></head>
<body>
<header><h1>trace <b>{title}</b> · {len(ctx.nodes)} nodes / {len(ctx.spans)} spans · errors {n_err}</h1>
<nav class="switch"><span>侧重点</span><button data-perspective="full" class="active">完整</button>{agent_button}</nav>
<nav class="switch"><span>形态</span><button data-layout="tree" class="active">调用栈</button><button data-layout="flame">火焰图</button></nav>
<button id="expand">全部展开</button><button id="fold">全部折叠</button></header>
<div class="wrap" id="view-stack">
  <div class="tree" id="tree"></div>
  <div class="pane" id="pane"></div>
</div>
<div id="view-flame"><div class="faxis" id="faxis"></div><div class="flame" id="flame"></div></div>
<script>{js}</script>
</body></html>
"""
