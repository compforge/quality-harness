"""Render a ``common.common.report_kit.doc.Report`` to a single self-contained HTML string.

One file, no build step. CSS is inlined; tables are fully server-rendered (sort is a
tiny vanilla-JS helper, score heat is computed here, so tables work offline). Charts and graphs use pinned, packaged browser assets; rendering and viewing need no network.
"""

from __future__ import annotations

import html
import json
from importlib.resources import files

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

_CSS = files("harness_common.report_kit").joinpath("assets/document/style.css").read_text()

# Vanilla click-to-sort: numeric-aware, toggles asc/desc, stable-ish. One copy for all tables.
_SORT_JS = files("harness_common.report_kit").joinpath("assets/document/sort.js").read_text()


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


def render_html(report: Report) -> str:
    """Render a Report to one self-contained HTML document string."""
    chart_counter = [0]
    meta = "".join(f"<span><b>{_esc(k)}:</b> {_esc(v)}</span>" for k, v in report.meta)
    sections = "".join(_section_html(s, chart_counter) for s in report.sections)

    if report.tabs:
        sections += _tabs_html(report.tabs, chart_counter)

    head_extra = ""
    if report.has_charts():
        head_extra = asset_script("vendor/echarts.min.js")
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
        + (asset_script("tabs/view.js") if report.tabs else "")
        + "</body></html>"
    )
