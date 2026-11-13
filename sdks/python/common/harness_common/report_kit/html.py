"""Render a ``common.common.report_kit.doc.Report`` to a single self-contained HTML string.

One file, no build step. CSS is inlined; tables are fully server-rendered (sort is a
tiny vanilla-JS helper, score heat is computed here, so tables work offline). Charts and graphs use pinned, packaged browser assets; rendering and viewing need no network.
"""

from __future__ import annotations

import html
import json

from harness_common.report_kit.doc import (
    Chart,
    GraphView,
    Heading,
    KV,
    LineSeries,
    Prose,
    Report,
    ReportTab,
    Section,
    Table,
)

from harness_common.report_kit.graph import asset_script, graph_scripts, render_graph

_CSS = """
:root { --fg:#1f2328; --muted:#656d76; --line:#d0d7de; --accent:#0969da; --zebra:#f6f8fa; }
* { box-sizing: border-box; }
body { font: 14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
       color: var(--fg); margin: 0; padding: 2rem; max-width: 1200px; }
body:has([role="tabpanel"][data-wide="true"]:not([hidden])) { max-width:none; }
[role="tabpanel"][data-wide="true"] .graph-canvas { height:max(600px, 78vh); }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.2rem; margin: 2rem 0 .75rem; padding-bottom: .3rem; border-bottom: 1px solid var(--line); }
h3 { font-size: 1.05rem; margin: 1.5rem 0 .5rem; }
/* sections are collapsible <details>; style <summary> like an h2 with a flip triangle */
details { margin: 0 0 .5rem; }
summary { font-size: 1.2rem; font-weight: 600; margin: 2rem 0 .75rem; padding-bottom: .3rem;
          border-bottom: 1px solid var(--line); cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: "\\25BE"; color: var(--muted); font-size: .85em; margin-right: .45rem; }
details:not([open]) > summary::before { content: "\\25B8"; }
summary:hover { color: var(--accent); }
.graph-filter-control > summary { font:inherit; margin:0; padding:1px 6px; border:1px solid #767676;
  border-radius:3px; background:#f5f5f5; white-space:nowrap; }
.graph-filter-control > summary::before { content:none; }
.graph-filter-control > summary::after { content:" ▾"; }

/* nested sub-section (a Heading group, e.g. one service): h3-sized, no rule line */
details.sub { margin: 0 0 .25rem .25rem; }
details.sub > summary { font-size: 1.05rem; margin: 1.25rem 0 .4rem; padding-bottom: 0;
                        border-bottom: none; }
.meta { color: var(--muted); font-size: .85rem; margin-bottom: 1rem; }
.meta span { margin-right: 1.25rem; }
dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem; margin: .5rem 0; }
dl.kv dt { color: var(--muted); }
dl.kv dd { margin: 0; font-variant-numeric: tabular-nums; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; font-size: .85rem; }
th, td { border: 1px solid var(--line); padding: .35rem .6rem; text-align: right; font-variant-numeric: tabular-nums; }
th:first-child, td:first-child { text-align: left; }
thead th { background: var(--zebra); cursor: pointer; user-select: none; position: sticky; top: 0; white-space: nowrap; }
thead th:hover { color: var(--accent); }
thead th::after { content: " \\2195"; color: var(--line); font-size: .8em; }
thead th.tip { text-decoration: underline dotted var(--muted); text-underline-offset: 3px; position: relative; }
/* instant CSS tooltip (avoids the laggy native-title hover delay): shown on :hover via ::before, below the header */
thead th.tip:hover::before {
  content: attr(data-tip);
  position: absolute; left: 0; top: 100%; z-index: 30; margin-top: 4px;
  width: max-content; max-width: 320px; white-space: normal; text-align: left;
  font: 400 .8rem/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  color: #fff; background: #24292f; padding: .4rem .6rem; border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0,0,0,.18); pointer-events: none;
}
/* tree column (Table.tree_col): box-drawing prefixes only line up when whitespace is
   preserved and the font is monospace; default right-align would break the indent. */
td.tree { text-align: left; white-space: pre;
          font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
tbody tr:nth-child(even) { background: var(--zebra); }
tr.winner { background: #dafbe1 !important; font-weight: 600; }
tr.knee   { background: #fff1e5 !important; }
.chart { width: 100%; height: 340px; margin: .5rem 0 1.5rem; }
[hidden] { display: none !important; }
.report-tabs { display:flex; gap:.5rem; border-bottom:1px solid var(--line); margin:1rem 0; }
.report-tabs [role="tab"] { padding:.65rem 1.2rem; background:none; border:0;
  border-bottom:3px solid transparent; font:inherit; cursor:pointer; color:var(--muted); }
.report-tabs [aria-selected="true"] { color:var(--accent); border-color:var(--accent); font-weight:600; }
.report-tabs [role="tab"]:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.graph-table > [data-graph-frame] { overflow-x:auto; }
.graph-viewport { position:relative; margin-top:.5rem; }
.graph-canvas { height:600px; border:1px solid var(--line); }
.graph-zoom { position:absolute; bottom:12px; right:12px; display:flex; align-items:center;
  gap:.5rem; padding:.45rem .65rem; background:white; border:1px solid var(--line);
  border-radius:6px; box-shadow:0 2px 6px #0001; }
.graph-zoom input { width:140px; }
.graph-zoom output { min-width:3.5em; text-align:right; font-variant-numeric:tabular-nums; }
p { margin: .5rem 0; }
code { background: var(--zebra); padding: .1rem .3rem; border-radius: 3px; font-size: .85em; }
"""

# Vanilla click-to-sort: numeric-aware, toggles asc/desc, stable-ish. One copy for all tables.
_SORT_JS = """
function rkSort(th){
  var table=th.closest('table'), tb=table.tBodies[0];
  var idx=Array.prototype.indexOf.call(th.parentNode.children, th);
  var asc=!(th.dataset.asc==='1'); th.dataset.asc=asc?'1':'0';
  var rows=Array.prototype.slice.call(tb.rows);
  rows.sort(function(a,b){
    var x=a.cells[idx].dataset.v, y=b.cells[idx].dataset.v;
    var nx=parseFloat(x), ny=parseFloat(y);
    var both=!isNaN(nx)&&!isNaN(ny);
    var c = both ? (nx-ny) : (x<y?-1:x>y?1:0);
    return asc?c:-c;
  });
  rows.forEach(function(r){ tb.appendChild(r); });
}
"""


def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _inline(text: str) -> str:
    """Escape text, then render markdown inline code spans (`` `x` `` → ``<code>x</code>``).

    The one bit of inline markdown a Prose block supports — matches the ``code`` CSS already
    in the sheet and keeps the HTML in step with the markdown twin (where backticks render
    natively). Split on backticks: odd segments sit between a pair, so they become ``<code>``;
    each segment is escaped independently so the content is always safe.
    """
    parts = text.split("`")
    return "".join(
        f"<code>{html.escape(p)}</code>" if i % 2 == 1 else html.escape(p)
        for i, p in enumerate(parts)
    )


def _heat_style(cell: str) -> str:
    """Red→yellow→green background for a score in [0,1]; '' if the cell isn't a score."""
    try:
        v = float(cell)
    except (TypeError, ValueError):
        return ""
    v = max(0.0, min(1.0, v))
    # 0 → red (0deg), 0.5 → yellow (60deg), 1 → green (120deg); pale so text stays readable.
    hue = int(120 * v)
    return f' style="background:hsl({hue},70%,88%)"'


def _table_html(t: Table) -> str:
    heat = set(t.heat_cols)
    head_cells = []
    for c in t.columns:
        tip = t.col_tips.get(c)
        # a tipped header carries the text in data-tip (not native title=, whose ~1s browser
        # delay feels laggy) — CSS shows it instantly on :hover. class marks it hoverable.
        attrs = f' class="tip" data-tip="{_esc(tip)}"' if tip else ""
        head_cells.append(f'<th onclick="rkSort(this)"{attrs}>{_esc(c)}</th>')
    head = "".join(head_cells)
    body: list[str] = []
    for i, row in enumerate(t.rows):
        cls = t.highlight.get(i)
        tr = f'<tr class="{_esc(cls)}">' if cls else "<tr>"
        cells = []
        for j, cell in enumerate(row):
            # data-v drives numeric-aware sort; heat columns also get a bg from the value.
            style = _heat_style(cell) if j in heat else ""
            cls_attr = ' class="tree"' if j == t.tree_col else ""
            cells.append(
                f'<td data-v="{_esc(cell)}"{style}{cls_attr}>{_esc(cell)}</td>'
            )
        body.append(tr + "".join(cells) + "</tr>")
    sort_attr = ""
    if t.sort_default:
        col, direction = t.sort_default
        sort_attr = f' data-sort-col="{col}" data-sort-dir="{_esc(direction)}"'
    return (
        f"<table{sort_attr}><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _chart_html(c: Chart, idx: int) -> str:
    div_id = f"rkchart{idx}"

    def _line(s: LineSeries, extra_style: dict | None = None) -> dict:
        out: dict = {
            "name": s.name,
            "type": "line",
            "showSymbol": False,
            "smooth": True,
            "data": [[x, y] for x, y in s.points],
        }
        style = dict(extra_style or {})
        if s.color:
            # itemStyle colors the legend/symbols, lineStyle the stroke — both, so a
            # pinned color is consistent everywhere it shows
            out["itemStyle"] = {"color": s.color}
            style["color"] = s.color
        if style:
            out["lineStyle"] = style
        return out

    series = [_line(s) for s in c.series]
    y_axis: object = {"type": "value", "name": c.y_label}
    if c.right_series:
        # second y axis for the context overlay; right-side lines are DASHED so the
        # eye separates measurement (left, solid) from context (right) at a glance
        y_axis = [
            {"type": "value", "name": c.y_label},
            {"type": "value", "name": c.y2_label, "splitLine": {"show": False}},
        ]
        series += [
            {**_line(s, {"type": "dashed"}), "yAxisIndex": 1} for s in c.right_series
        ]
    tips = {s.name: s.tip for s in [*c.series, *c.right_series] if s.tip}
    legend: dict = {"top": 24, "type": "scroll"}
    if tips:
        # hover a legend item → the signal's human meaning (series names are
        # addressing ids; the tip says what they ARE). The formatter is a JS
        # function — injected via sentinel replacement since the option is JSON.
        legend["tooltip"] = {"show": True, "formatter": "__RK_TIP_FORMATTER__"}
    x_axis = {
        "type": c.x_kind,
        "name": c.x_label,
        "nameLocation": "middle",
        "nameGap": 28,
    }
    option = {
        "title": {"text": c.title, "left": "center", "textStyle": {"fontSize": 13}},
        "tooltip": {"trigger": "axis"},
        "legend": legend,
        "grid": {"top": 64, "left": 56, "right": 56, "bottom": 48},
        "xAxis": x_axis,
        "yAxis": y_axis,
        "dataZoom": [{"type": "inside"}, {"type": "slider", "height": 16, "bottom": 8}],
        "series": series,
    }
    payload = json.dumps(option, ensure_ascii=False)
    if tips:
        fn = (
            "function(p){var t=" + json.dumps(tips, ensure_ascii=False) + ";"
            "return t[p.name]||p.name;}"
        )
        payload = payload.replace('"__RK_TIP_FORMATTER__"', fn)
    return (
        f'<div id="{div_id}" class="chart"></div>'
        f"<script>echarts.init(document.getElementById('{div_id}')).setOption({payload});</script>"
    )


def _block_html(block: object, chart_counter: list[int]) -> str:
    if isinstance(block, Prose):
        return f"<p>{_inline(block.text)}</p>"
    if isinstance(block, Heading):
        return f"<h3>{_esc(block.text)}</h3>"
    if isinstance(block, KV):
        items = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in block.items)
        return f'<dl class="kv">{items}</dl>'
    if isinstance(block, Table):
        return _table_html(block)
    if isinstance(block, GraphView):
        idx = chart_counter[0]
        chart_counter[0] += 1
        return render_graph(block, idx, _table_html)
    if isinstance(block, Chart):
        idx = chart_counter[0]
        chart_counter[0] += 1
        return _chart_html(block, idx)
    return ""


def _section_html(s: Section, chart_counter: list[int]) -> str:
    # collapsible via native <details> (zero JS). `open` by default so charts init at
    # full size (a chart in a collapsed <details> would render into a 0-height div).
    # A Heading starts a nested collapsible sub-section that runs until the next
    # Heading — so per-service groups inside §3/§4 fold independently.
    parts: list[str] = []
    i = 0
    while i < len(s.blocks) and not isinstance(s.blocks[i], Heading):
        parts.append(_block_html(s.blocks[i], chart_counter))
        i += 1
    while i < len(s.blocks):
        head = s.blocks[i]
        i += 1
        group: list[str] = []
        while i < len(s.blocks) and not isinstance(s.blocks[i], Heading):
            group.append(_block_html(s.blocks[i], chart_counter))
            i += 1
        parts.append(
            f'<details class="sub" open><summary>{_esc(head.text)}</summary>'
            f"{''.join(group)}</details>"
        )
    return (
        f"<details open><summary>{_esc(s.heading)}</summary>{''.join(parts)}</details>"
    )


def _tabs_html(tabs: list[ReportTab], chart_counter: list[int]) -> str:
    buttons, panels = [], []
    for index, tab in enumerate(tabs):
        tab_id, panel_id = f"rk-tab-{index}", f"rk-panel-{index}"
        active = index == 0
        buttons.append(
            f'<button type="button" role="tab" id="{tab_id}" aria-controls="{panel_id}" '
            f'aria-selected="{str(active).lower()}" tabindex="{0 if active else -1}">'
            f"{_esc(tab.title)}</button>"
        )
        content = "".join(
            _section_html(section, chart_counter) for section in tab.sections
        )
        wide_attr = ' data-wide="true"' if tab.wide else ""
        panels.append(
            f'<div role="tabpanel" id="{panel_id}" aria-labelledby="{tab_id}"'
            f"{wide_attr}"
            f"{'' if active else ' hidden'}>{content}</div>"
        )
    return (
        '<div class="report-tabs" role="tablist" aria-label="Report views">'
        + "".join(buttons)
        + "</div>"
        + "".join(panels)
    )


_TABS_JS = """
document.querySelectorAll('.report-tabs').forEach(list => {
  const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
  function activate(tab) {
    tabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
    });
    // Charts initialized in a hidden panel need their visible dimensions on activation.
    document.querySelectorAll('[role="tabpanel"]:not([hidden]) .chart').forEach(el => {
      echarts.getInstanceByDom(el)?.resize();
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      tabs[next].focus();
      activate(tabs[next]);
    });
  });
});
"""


def render_html(report: Report) -> str:
    """Render a Report to one self-contained HTML document string."""
    chart_counter = [0]
    meta = "".join(f"<span><b>{_esc(k)}:</b> {_esc(v)}</span>" for k, v in report.meta)
    sections = "".join(_section_html(s, chart_counter) for s in report.sections)

    if report.tabs:
        sections += _tabs_html(report.tabs, chart_counter)

    head_extra = ""
    if report.has_charts():
        head_extra = asset_script("echarts.min.js")
    if report.has_graphs():
        head_extra += graph_scripts()

    # Apply each table's default sort once the DOM + sort helper are ready.
    init = (
        "<script>document.addEventListener('DOMContentLoaded',function(){"
        "document.querySelectorAll('table[data-sort-col]').forEach(function(t){"
        "var c=+t.dataset.sortCol, th=t.tHead.rows[0].cells[c];"
        "if(t.dataset.sortDir==='desc'){th.dataset.asc='1';}else{th.dataset.asc='0';}"
        "rkSort(th);});});</script>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_esc(report.title)}</title>"
        f"<style>{_CSS}</style>{head_extra}"
        f"<script>{_SORT_JS}</script>"
        "</head><body>"
        f"<h1>{_esc(report.title)}</h1>"
        f'<div class="meta">{meta}</div>'
        f"{sections}{init}"
        + (f"<script>{_TABS_JS}</script>" if report.tabs else "")
        + "</body></html>"
    )
