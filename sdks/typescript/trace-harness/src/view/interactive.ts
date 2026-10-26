import type { TraceView } from "./model";
import { TraceArchive, ARCHIVE_SCRIPT } from "./archive";
import echartsSource from "echarts/dist/echarts.min.js" with { type: "text" };
import { MEASUREMENT_DIALOG_SCRIPT, MEASUREMENT_DIALOG_HTML, MEASUREMENT_DIALOG_CSS } from "./measurement-dialog";
import renderJsonSource from "renderjson/renderjson.js" with { type: "text" };



function htmlEscape(value: unknown): string {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[char]!);
}

const CSS = String.raw`
*{box-sizing:border-box}:root{--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
body{font:13px/1.5 var(--mono);margin:0;background:#f6f7f9;color:#1a1a1a}
header{background:#1f2937;color:#fff;padding:8px 16px;display:flex;gap:12px;align-items:center}

.trace-search{display:flex;align-items:center;gap:8px;padding:7px 16px;background:#eef2f7;height:42px}
.trace-search input{width:280px;max-width:45vw;font:12px var(--mono);padding:4px 8px;border:1px solid #94a3b8;border-radius:4px}
.trace-search button{font:12px var(--mono);cursor:pointer}.trace-search button:disabled{cursor:default;opacity:.45}
#search-status{font-size:12px;color:#475569}
.row.search-hit{background:#fef3c7;box-shadow:inset 3px 0 #d97706}
.row.search-hit.search-current{background:#fde68a;outline:1px solid #d97706;outline-offset:-1px}
.fcell.search-hit{outline:2px solid #f59e0b;outline-offset:-2px;filter:brightness(1.2)}

header h1{font-size:13px;margin:0;font-weight:normal}header b{color:#93c5fd}
header button{font:11px var(--mono);background:#374151;color:#e5e7eb;border:0;border-radius:4px;padding:2px 8px;cursor:pointer}
nav.switch{display:flex;gap:2px;align-items:center}nav.switch span{font-size:10px;color:#9ca3af;margin-right:2px}
nav.switch button.active{background:#2563eb;color:#fff}
.wrap{--tree-width:min(52%,760px);display:flex;height:calc(100vh - 77px)}#view-flame{display:none;height:calc(100vh - 77px);overflow:auto;background:#fff;padding:12px 16px}
.tree{flex:0 0 var(--tree-width);min-width:0;overflow:auto;background:#fff;padding:8px 0}
.splitter{flex:0 0 6px;background:#e5e7eb;cursor:col-resize;touch-action:none;position:relative;outline:0}.splitter::after{content:"";position:absolute;inset:0 -3px}.splitter:hover,.splitter.dragging,.splitter:focus-visible{background:#3b82f6}body.split-resizing{cursor:col-resize;user-select:none}
.pane{flex:1;min-width:0;overflow:auto;padding:16px}.row{white-space:nowrap;cursor:pointer;font-size:12px;padding:2px 8px;border-left:3px solid transparent;display:flex;align-items:baseline;gap:6px;position:relative}.row.agent-row::before{content:"";position:absolute;left:var(--node-indent,4px);top:3px;bottom:3px;width:3px;border-radius:2px;background:var(--node-color,#9ca3af)}
.measurement-marker{border:1px solid #d1d5db;background:#fff;padding:0 6px;height:22px;display:inline-flex;align-items:center;justify-content:center;gap:3px;flex:none;cursor:pointer;border-radius:4px;align-self:center;color:#475569;font:11px var(--mono)}
.measurement-marker:hover{background:#fef3c7}.measurement-marker:focus-visible{outline:2px solid #ca8a04;outline-offset:1px}
.row:hover{background:#f1f5f9}.row.sel{background:#e0edff;border-left-color:#3b82f6}.row.err{color:#b91c1c}.row.err.sel{background:#fee2e2;border-left-color:#dc2626}
.tw{display:inline-block;width:14px;color:#9ca3af;cursor:pointer;text-align:center;flex:none}.kind{font-size:10px;border-radius:3px;padding:0 5px;flex:none;color:#fff;background:#9ca3af}
.kind.agent,.kind.session{background:#7c3aed}.kind.turn{background:#4f46e5}.kind.framework,.kind.node{background:#2563eb}.kind.model_call,.kind.model-call{background:#059669}.kind.tool_call,.kind.tool-call{background:#d97706}.kind.action,.kind.operation{background:#0891b2}.kind.service{background:#6b7280}
.dur{color:#6b7280;flex:none}.brief{color:#0d9488;font-size:11px;overflow:hidden;text-overflow:ellipsis}.errdot{color:#dc2626;font-weight:bold;flex:none}
.pane h2{font-size:14px;margin:0 0 4px}.meta{color:#6b7280;font-size:12px;margin-bottom:12px}.findings{margin:0 0 14px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:4px;background:#fff}
.findings div{font-size:12px;margin:2px 0}.findings .f-error{color:#b91c1c}.findings .f-warn{color:#b45309}.findings .f-info{color:#6b7280}
.measurement-section{background:#fff;border:1px solid #dbe2ea;border-radius:6px;padding:16px;margin:18px 0 24px}
.measurement-section h3,.facts-section h3{font-size:14px;margin:0 0 14px}
.measurement-group+.measurement-group{border-top:1px solid #e5e7eb;margin-top:18px;padding-top:18px}
.measurement-group h4{font-size:13px;margin:0 0 6px;overflow-wrap:anywhere}
.measurement-scope,.measurement-note{font-size:12px;color:#64748b;line-height:1.6;margin:0 0 12px}
.measurement-note{margin:10px 0 0}.measurement-table-wrap{overflow-x:auto}
.measurement-table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.5}
.measurement-table th,.measurement-table td{padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:left;white-space:nowrap}
.measurement-table thead th{background:#f8fafc;color:#475569;font-weight:600}
.measurement-table tbody th{font-weight:400}.measurement-table tbody tr:last-child>*{border-bottom:0}
.measurement-table .numeric{text-align:right;font-variant-numeric:tabular-nums}
.measurement-table .measurement-status{white-space:normal;min-width:100px}
.facts-section{border-top:1px solid #dbe2ea;padding-top:16px;margin-top:20px}
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
const SCRIPT = `${renderJsonSource}\n${MEASUREMENT_DIALOG_SCRIPT}\n${String.raw`
const {trees:TREES,spans:SPANS}=readTraceEntry('index.json');
const KCOLOR={agent:'#7c3aed',session:'#7c3aed',turn:'#4f46e5',framework:'#2563eb',node:'#2563eb',model_call:'#059669','model-call':'#059669',tool_call:'#d97706','tool-call':'#d97706',action:'#0891b2',operation:'#0891b2',service:'#6b7280'};
const stackEl=document.getElementById('view-stack'),treeEl=document.getElementById('tree'),splitterEl=document.getElementById('splitter'),paneEl=document.getElementById('pane');
let perspective=Object.keys(TREES)[0],layout='tree',tree=TREES[perspective],selectedId=location.hash.slice(1);
let byId={},parentOf={},boxOf={},twOf={},flameBuilt=false,stackMaxDuration=1;
let searchValues;
function nodeSearchValue(id){searchValues??=readTraceEntry('values.json');return searchValues[id]??'';}

// Search only business-selected Node.value; virtual groups and Session observation items have no value.
const searchInput=document.getElementById('node-search');
const searchStatus=document.getElementById('search-status');
const searchPrev=document.getElementById('search-prev'),searchNext=document.getElementById('search-next');
let searchHits=[],searchIndex=-1;
function paintSearch(){
  const hits=new Set(searchHits);
  document.querySelectorAll('.row[data-id],.fcell[data-id]').forEach(row=>{
    row.classList.toggle('search-hit',hits.has(row.dataset.id));
    row.classList.toggle('search-current',row.dataset.id===searchHits[searchIndex]);
  });
  searchStatus.textContent=perspective!=='default'||!searchInput.value.trim()?'':searchHits.length
    ?(searchIndex<0?'':(searchIndex+1)+' / ')+searchHits.length+' 个命中':'无匹配节点';
  searchPrev.disabled=searchNext.disabled=!searchHits.length;
}
function updateSearch(){
  const enabled=perspective==='default';
  searchInput.disabled=!enabled;
  searchInput.placeholder=enabled?'搜索节点内容':'节点搜索请切换到完整视图';
  const keyword=enabled?searchInput.value.trim().toLowerCase():'';
  searchHits=keyword?Object.keys(byId).filter(id=>nodeSearchValue(id).toLowerCase().includes(keyword)):[];
  searchIndex=-1;
  for(const id of searchHits)unfold(id);
  paintSearch();
}
function moveSearch(step){
  if(!searchHits.length)return;
  searchIndex=searchIndex<0?(step<0?searchHits.length-1:0)
    :(searchIndex+step+searchHits.length)%searchHits.length;
  showLayout('tree');select(searchHits[searchIndex]);paintSearch();
  treeEl.querySelector('.row.search-current')?.scrollIntoView({block:'center'});
}
searchInput.addEventListener('input',updateSearch);
searchInput.addEventListener('keydown',event=>{
  if(event.key==='Enter'){event.preventDefault();moveSearch(event.shiftKey?-1:1);}
  if(event.key==='Escape'){searchInput.value='';updateSearch();}
});
searchPrev.onclick=()=>moveSearch(-1);searchNext.onclick=()=>moveSearch(1);

renderjson.set_icons('▸','▾').set_show_to_level(1).set_max_string_length(256);
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
// Use a fixed-color SVG: platform emoji fonts do not preserve the balloon color.
function appendMeasurementMarker(row,n){
  if(!n.has_measurements)return;
  const marker=document.createElement('button');marker.type='button';marker.className='measurement-marker';
  marker.title='查看调用聚合与时间线';marker.setAttribute('aria-label','查看耗时分析');marker.setAttribute('aria-haspopup','dialog');
  marker.innerHTML='<svg width="16" height="20" viewBox="0 0 16 20" aria-hidden="true" focusable="false"><path d="M8 14c-3 2 3 3 0 5" fill="none" stroke="#78716c" stroke-width="1"/><path d="m8 12-2 3h4z" fill="#facc15" stroke="#ca8a04" stroke-width=".7"/><ellipse cx="8" cy="6.5" rx="5.5" ry="6" fill="#facc15" stroke="#ca8a04" stroke-width=".8"/><path d="M5 3.5c-1 .7-1.4 1.6-1.4 2.5" fill="none" stroke="#fef9c3" stroke-width="1.5" stroke-linecap="round"/></svg><span>耗时分析</span>';
  marker.onclick=event=>{event.stopPropagation();openMeasurements(n);};
  row.appendChild(marker);
}
function renderInto(n,depth,parent){byId[n.node_id]=n;parentOf[n.node_id]=parent&&parent.node_id;const box=document.createElement('div'),row=document.createElement('div');row.className='row'+(n.has_error?' err':'');row.dataset.id=n.node_id;row.dataset.depth=String(depth);row.style.paddingLeft=(depth*16+8)+'px';const timedLeaf=perspective==='agent'&&!n.children.length,rowHeight=timedLeaf?timeHeight(n.duration_ms,stackMaxDuration):22;row.dataset.rowHeight=String(rowHeight);
// 竖条跟随 node 的缩进而非整行边框，避免选中态覆盖，也让 leaf 的时间高度直接可见。
if(perspective==='agent'){row.classList.add('agent-row');row.style.setProperty('--node-color',KCOLOR[n.kind]||'#9ca3af');row.style.setProperty('--node-indent',(depth*16+4)+'px');row.style.minHeight=rowHeight+'px';row.style.alignItems='center';}
const tw=document.createElement('span');tw.className='tw';tw.textContent=n.children.length?'▾':'·';row.appendChild(tw);if(n.kind){const k=document.createElement('span');k.className='kind '+n.kind;k.textContent=n.kind;row.appendChild(k);}const nm=document.createElement('span');nm.className='node-name';row.appendChild(nm);appendMeasurementMarker(row,n);applyNameLayout(row,n,depth,rowHeight);const dur=document.createElement('span');dur.className='dur';dur.textContent=(n.timing_known===false?'unknown':fmtMs(n.duration_ms));row.appendChild(dur);if(n.brief){const b=document.createElement('span');b.className='brief';b.textContent='('+n.brief+')';row.appendChild(b);}if(n.has_error){const e=document.createElement('span');e.className='errdot';e.textContent='[ERROR]';row.appendChild(e);}box.appendChild(row);const kids=document.createElement('div');box.appendChild(kids);boxOf[n.node_id]=kids;twOf[n.node_id]=tw;const mount=()=>{if(kids.dataset.mounted)return;kids.dataset.mounted='true';n.children.forEach(c=>kids.appendChild(renderInto(c,depth+1,n)));};kids.mount=mount;if(!(n.folded||n.collapsed))mount();if((n.folded||n.collapsed)&&n.children.length){kids.style.display='none';tw.textContent='▸';}tw.onclick=ev=>{ev.stopPropagation();const open=kids.style.display!=='none';if(!open)mount();kids.style.display=open?'none':'';tw.textContent=n.children.length?(open?'▸':'▾'):'·';};row.onclick=()=>select(n.node_id);return box;}
function previewText(text){return text.length>16384?text.slice(0,16384)+'\n…（预览已截断，可下载完整内容）':text;}
function factValue(v){const text=typeof v==='string'?v:JSON.stringify(v,null,2);return typeof v==='object'||text.length>160||text.includes('\n')?'<details><summary>'+esc(text.slice(0,120))+'</summary><pre>'+esc(previewText(text))+'</pre></details>':esc(text);}
function facts(n){const rows=Object.entries(n.facts||{});return rows.length?'<section class="facts-section"><h3>Facts</h3><table class="facts">'+rows.map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+factValue(v)+'</td></tr>').join('')+'</table></section>':'';}
function findings(n){const marks={error:'✗',warn:'▲',info:'·'};return(n.findings||[]).length?'<div class="findings">'+n.findings.map(f=>'<div class="f-'+esc(f.severity)+'">'+(marks[f.severity]||'·')+' ['+esc(f.source)+'] '+esc(f.note)+'</div>').join('')+'</div>':'';}
function details(n){const rows=Object.entries(n.details||{});return rows.length?'<div class="meta">详情：</div>'+rows.map(([k,v])=>'<div class="feat"><div class="feat-h">'+esc(k)+'</div><pre class="feat-body">'+esc(previewText(typeof v==='string'?v:JSON.stringify(v,null,2)))+'</pre></div>').join(''):'';}
function downloadValue(name,value){const blob=new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type:'text/plain;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function attrValue(payload){const dd=document.createElement('dd');if(payload&&payload.kind==='json'){dd.className='json';dd.appendChild(renderjson(payload.value));}else{const text=payload&&payload.kind==='text'?payload.value:String(payload??'');dd.textContent=previewText(text);if(text.length>16384){const button=document.createElement('button');button.textContent='下载完整内容';button.onclick=()=>downloadValue('attribute.txt',text);dd.appendChild(button);}}return dd;}
function unfold(id){const parents=[];for(let p=parentOf[id];p;p=parentOf[p])parents.unshift(p);for(const p of parents){const b=boxOf[p];if(b){b.mount?.();b.style.display='';twOf[p].textContent='▾';}}}
function measurementValue(value,unit){
  if(value==null)return '—';
  if(unit==='ms'&&typeof value==='number')return fmtMs(value);
  const text=typeof value==='object'?JSON.stringify(value):String(value);
  return esc(text)+(unit&&unit!=='call'?' '+esc(unit):'');
}
function measurementGroup(rows){
  const first=rows[0],prefix=first.kind==='calls_until_node_end',calls=first.kind==='calls_until_node_end';
  const label=calls?'累计调用':first.kind;
  const scope=prefix?'请求开始 → 此节点结束（含进行中的调用）':first.scope==='node'?'此节点内部':first.scope;
  const elapsed=prefix&&typeof first.evidence.start_ms==='number'&&typeof first.evidence.end_ms==='number'?fmtMs(first.evidence.end_ms-first.evidence.start_ms)+' wall-clock':'';
  const keys=[...new Set(rows.flatMap(r=>Object.keys(r.values)))];
  const names={count:'调用次数',duration_sum_ms:'累计耗时',covered_ms:'覆盖时间'};
  const dimension=rows.some(r=>r.call_kind),status=rows.some(r=>r.status!=='measured');
  const header=(dimension?'<th scope="col">Kind</th>':'')+keys.map(k=>'<th scope="col" class="numeric">'+esc(names[k]||k)+'</th>').join('')+(status?'<th scope="col">状态</th>':'');
  const body=rows.map(r=>'<tr>'+(dimension?'<th scope="row">'+esc(r.call_kind||'—')+'</th>':'')+keys.map(k=>'<td class="numeric">'+(r.status==='measured'?measurementValue(r.values[k],r.units[k]):'—')+'</td>').join('')+(status?'<td class="measurement-status">'+esc(r.status==='measured'?'已测量':r.status==='not_applicable'?'不适用':'错误：'+(r.error||'未知错误'))+'</td>':'')+'</tr>').join('');
  return '<div class="measurement-group"><h4>'+esc(label)+'</h4><p class="measurement-scope">'+esc(scope)+(elapsed?' · '+esc(elapsed):'')+'</p><div class="measurement-table-wrap"><table class="measurement-table"><thead><tr>'+header+'</tr></thead><tbody>'+body+'</tbody></table></div>'+(prefix?'<p class="measurement-note">累计耗时是各调用耗时之和；覆盖时间对重叠区间去重。各 kind 可重叠，不代表独占的 wall-clock 贡献。</p>':'')+'</div>';
}
function measurementBlock(n){
  const rows=n.measurements||[];if(!rows.length)return '';
  const groups=new Map();for(const row of rows){if(!groups.has(row.kind))groups.set(row.kind,[]);groups.get(row.kind).push(row);}
  return '<section class="measurement-section" aria-labelledby="measurements"><h3 id="measurements" tabindex="-1">Measurements</h3>'+[...groups.values()].map(measurementGroup).join('')+'</section>';
}
function select(id){document.querySelectorAll('.row.sel').forEach(r=>r.classList.remove('sel'));unfold(id);const row=document.querySelector('.row[data-id="'+CSS.escape(id)+'"]');if(row){row.classList.add('sel');row.scrollIntoView({block:'nearest'});}const summary=byId[id];if(!summary)return;const n=loadNode(summary);selectedId=id;location.hash=id;paneEl.innerHTML='<h2>'+esc(n.name)+'</h2><div class="meta">'+esc(n.kind)+' · '+(n.timing_known===false?'unknown':fmtMs(n.duration_ms))+(n.service?' · '+esc(n.service):'')+(n.has_error?' · <b style="color:#dc2626">ERROR：'+esc(n.error)+'</b>':'')+'</div>'+findings(n)+facts(n)+details(n)+'<div class="meta">溯源 span（'+n.span_ids.length+'）：</div><div class="chips">'+n.span_ids.map(sid=>'<span class="chip'+((n.error_span_ids||[]).includes(sid)?' errc':'')+'" data-sid="'+esc(sid)+'">'+(sid===n.primary_span_id?'primary':'卫星')+' · '+esc((SPANS[sid]||{}).operation||sid)+'</span>').join('')+'</div><div id="attrs"></div>';paneEl.querySelectorAll('.chip').forEach(c=>c.onclick=()=>showSpan(c.dataset.sid));paneEl.querySelectorAll('.feat-h').forEach(h=>h.onclick=()=>h.parentElement.classList.toggle('open'));const download=document.createElement('button');download.textContent='下载节点完整详情';download.onclick=()=>downloadValue('node.json',n);paneEl.appendChild(download);const sid=(n.error_span_ids||[])[0]||n.primary_span_id;if(sid)showSpan(sid);else document.getElementById('attrs').innerHTML='<div class="meta">视图压缩节点，无独立 span</div>';}
function showSpan(sid){paneEl.querySelectorAll('.chip').forEach(c=>c.classList.toggle('sel',c.dataset.sid===sid));const sp=loadSpan(sid),box=document.getElementById('attrs');box.replaceChildren();if(!sp){const missing=document.createElement('div');missing.className='meta';missing.textContent='span 不在快照内';box.appendChild(missing);return;}const meta=document.createElement('div');meta.className='meta';meta.textContent='span '+sid+' · '+sp.operation+' · '+fmtMs(sp.duration_ms);box.appendChild(meta);const attrs=document.createElement('dl');attrs.className='attrs';for(const [key,value] of Object.entries(sp.attrs)){const name=document.createElement('dt');name.textContent=key;attrs.append(name,attrValue(value));}box.appendChild(attrs);}
function firstError(ns){for(const n of ns){if(n.has_error&&n.kind)return n.node_id;const child=firstError(n.children);if(child)return child;}return null;}
function firstReal(ns){for(const n of ns){if(n.kind)return n.node_id;const child=firstReal(n.children);if(child)return child;}return null;}
function renderTree(){tree=TREES[perspective]||{roots:[]};treeEl.replaceChildren();stackMaxDuration=perspective==='agent'?maxLeafDuration(tree.roots):1;byId={};parentOf={};boxOf={};twOf={};flameBuilt=false;(function index(ns,parent){for(const n of ns){byId[n.node_id]=n;parentOf[n.node_id]=parent;index(n.children,n.node_id);}})(tree.roots,null);tree.roots.forEach(r=>treeEl.appendChild(renderInto(r,r.depth||0,null)));refreshTreeNames();if(!tree.roots.length){treeEl.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';paneEl.innerHTML='<div class="empty">此视图为空或尚未准备数据</div>';updateSearch();if(layout==='flame')buildFlame();return;}const wanted=selectedId&&byId[selectedId]?selectedId:null;select(wanted||firstError(tree.roots)||firstReal(tree.roots)||tree.roots[0].node_id);updateSearch();if(layout==='flame')buildFlame();}
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
document.getElementById('expand').onclick=()=>{for(const id of Object.keys(byId))unfold(id);};document.getElementById('fold').onclick=()=>treeEl.querySelectorAll('.tw').forEach(t=>{if(t.textContent==='▾')t.click();});
const views={tree:document.getElementById('view-stack'),flame:document.getElementById('view-flame')};
function showLayout(next){layout=next;Object.entries(views).forEach(([key,element])=>element.style.display=key===next?(key==='tree'?'flex':'block'):'none');document.querySelectorAll('[data-layout]').forEach(button=>button.classList.toggle('active',button.dataset.layout===next));document.getElementById('expand').style.display=next==='tree'?'':'none';document.getElementById('fold').style.display=next==='tree'?'':'none';if(next==='tree')requestAnimationFrame(()=>scheduleTreeWidth(treeEl.getBoundingClientRect().width));if(next==='flame'&&!flameBuilt)buildFlame();}
function showPerspective(next){perspective=next;document.querySelectorAll('[data-perspective]').forEach(button=>button.classList.toggle('active',button.dataset.perspective===next));renderTree();}
document.querySelectorAll('[data-layout]').forEach(button=>button.onclick=()=>showLayout(button.dataset.layout));document.querySelectorAll('[data-perspective]').forEach(button=>button.onclick=()=>showPerspective(button.dataset.perspective));
// @spec Paged views retain absolute row.depth; missing ancestors do not reset flame rows.
function buildFlame(){const box=document.getElementById('flame'),axis=document.getElementById('faxis');box.replaceChildren();axis.replaceChildren();if(!tree.roots.length){box.innerHTML='<div class="empty">当前侧重点没有可展示节点</div>';flameBuilt=true;return;}let t0=Infinity,t1=-Infinity,maxD=0;(function scan(ns){ns.forEach(n=>{if(n.timing_known!==false){t0=Math.min(t0,n.start_ms);t1=Math.max(t1,n.start_ms+n.duration_ms);}maxD=Math.max(maxD,n.depth);scan(n.children);});})(tree.roots);if(!Number.isFinite(t0)||!Number.isFinite(t1)){box.innerHTML='<div class="empty">没有已记录的完整时间区间</div>';flameBuilt=true;return;}const span=Math.max(t1-t0,1e-6),rowH=20;box.style.height=((maxD+1)*rowH+4)+'px';for(let i=0;i<=10;i++){const s=document.createElement('span');s.style.left=(i*10)+'%';s.textContent=fmtMs(span*i/10);axis.appendChild(s);}(function place(ns){ns.forEach(n=>{if(n.timing_known===false){place(n.children);return;}const c=document.createElement('div');c.dataset.id=n.node_id;c.className='fcell'+(n.has_error?' err':'');c.style.left=((n.start_ms-t0)/span*100)+'%';c.style.width=Math.max(n.duration_ms/span*100,.15)+'%';c.style.top=(n.depth*rowH)+'px';c.style.background=KCOLOR[n.kind]||'#9ca3af';box.appendChild(c);c.textContent=nameForBudget(n,Math.max(1,Math.floor(c.clientWidth/7)));c.title=compactName(n,1)[0]+' · '+n.kind+' · '+(n.timing_known===false?'unknown':fmtMs(n.duration_ms))+(n.service?' · '+n.service:'')+(n.has_error?' · ERROR':'');c.onclick=()=>{showLayout('tree');select(n.node_id);};place(n.children);});})(tree.roots);flameBuilt=true;paintSearch();}
initMeasurementDialog();renderTree();showLayout('tree');
`}`;

/** HTML serializes prepared views; it has no access to TraceContext or analysis services. */
export function renderTraceHtml(views: readonly TraceView[]): string {
  if (!views.length) throw new Error("HTML requires a trace view");
  const first = views[0]!;
  if (views.some(view => view.trace_id !== first.trace_id)) throw new Error("HTML views must belong to the same trace");
  if (new Set(views.map(view => view.view)).size !== views.length) throw new Error("Duplicate trace view");
  const trees = Object.fromEntries(views.map(view => {
    const nodes = new Map(view.rows.items.map(row => [row.node_id, { ...row, timing: view.timings[row.node_id], children: [] as Record<string, unknown>[] }]));
    const roots: Record<string, unknown>[] = [];
    for (const row of view.rows.items) {
      const node = nodes.get(row.node_id)!;
      const parent = row.parent_id ? nodes.get(row.parent_id) : undefined;
      if (parent) parent.children.push(node); else roots.push(node);
    }
    return [view.view, { roots }];
  }));
  const archive = new TraceArchive();
  let sequence = 0;
  const splitNode = (node: Record<string, unknown>): Record<string, unknown> => {
    const { children, facts, details, findings, measurements, timing, error, name, ...skeleton } = node;
    const payload = archive.add(`nodes/${sequence++}.json`, { facts, details, findings, measurements, timing, error, name });
    return { ...skeleton, name: String(name ?? "").slice(0, 256),
      name_variants: [...new Set((node.name_variants as string[] ?? []).map(value => value.slice(0, 256)))],
      brief: String(node.brief ?? "").slice(0, 256),
      payload, has_measurements: Array.isArray(measurements) && measurements.length > 0,
      children: (children as Record<string, unknown>[]).map(splitNode) };
  };
  const skeletons = Object.fromEntries(Object.entries(trees).map(([key, tree]) => [key, { roots: tree.roots.map(splitNode) }]));
  const allSpans = Object.assign({}, ...views.map(view => view.spans)) as Record<string, Record<string, unknown>>;
  const spans = Object.fromEntries(Object.entries(allSpans).map(([id, span], i) => [id, {
    operation: String(span.operation ?? "").slice(0, 256), payload: archive.add(`spans/${i}.json`, span),
  }]));
  archive.add("values.json", Object.assign({}, ...views.map(view => view.values)));
  archive.add("index.json", { trees: skeletons, spans });
  if (views.some(view => Object.values(view.timings).some(timing => timing.status === "measured"))) {
    archive.add("measurement-chart.json", echartsSource);
  }
  const script = ARCHIVE_SCRIPT + SCRIPT;
  const title = htmlEscape(first.trace_id);
  const errorCount = first.error_count;
  const buttons = views.map((view, index) => `<button data-perspective="${view.view}"${index === 0 ? ' class="active"' : ""}>${view.view === "agent" ? "Agent" : "完整"}</button>`).join("");
  const traceMeasurements = first.measurements.length ? `<details><summary>Trace measurements</summary><pre>${htmlEscape(JSON.stringify(first.measurements, null, 2))}</pre></details>` : "";
  const pageInfo = views.map(view => `${view.view}: ${view.rows.items.length}/${view.rows.total} rows; offset=${view.rows.offset}; next=${view.rows.next_offset ?? "none"}; ${view.status}`).join(" · ");
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>trace ${title}</title><style>${CSS}${MEASUREMENT_DIALOG_CSS}</style></head>
<body><header><h1>trace <b>${title}</b> · ${first.node_count} nodes / ${first.span_count} spans · errors ${errorCount}</h1>
<nav class="switch"><span>侧重点</span>${buttons}</nav>
<nav class="switch"><span>形态</span><button data-layout="tree" class="active">调用栈</button><button data-layout="flame">火焰图</button></nav><button id="expand">全部展开</button><button id="fold">全部折叠</button></header>
<div class="meta">${htmlEscape(pageInfo)}</div>${traceMeasurements}
<div class="trace-search" role="search"><input id="node-search" type="search" aria-label="搜索节点内容" placeholder="搜索节点内容" title="搜索业务指定的节点内容；Enter 下一个，Shift+Enter 上一个，Esc 清空"><button id="search-prev" aria-label="上一个匹配节点" disabled>↑</button><button id="search-next" aria-label="下一个匹配节点" disabled>↓</button><span id="search-status" role="status" aria-live="polite"></span></div>
<div class="wrap" id="view-stack"><div class="tree" id="tree"></div><div class="splitter" id="splitter" role="separator" aria-label="调整调用栈与节点详情宽度" aria-orientation="vertical" tabindex="0"></div><div class="pane" id="pane"></div></div>
<div id="view-flame"><div class="faxis" id="faxis"></div><div class="flame" id="flame"></div></div>${MEASUREMENT_DIALOG_HTML}${archive.embed()}<script>${script.replace(/<\/script/gi, "<\\/script")}</script></body></html>\n`;
}
