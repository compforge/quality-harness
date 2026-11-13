"""Offline Cytoscape renderer; inputs are already reduced, domain-neutral view data."""

import html
import json
from collections.abc import Callable
from dataclasses import asdict
from importlib.resources import files

from harness_common.report_kit.doc import GraphView, Table


def asset_script(name: str) -> str:
    # Inline scripts must not contain an HTML closing-script delimiter.
    assets = files("harness_common.report_kit").joinpath("assets")
    notices = {
        "cytoscape.min.js": ("cytoscape-LICENSE",),
        "elk.bundled.js": ("elk-LICENSE.md",),
        "echarts.min.js": ("echarts-LICENSE", "echarts-NOTICE"),
    }
    license_text = "\n".join(
        assets.joinpath(item).read_text() for item in notices.get(name, ())
    )
    code = assets.joinpath(name).read_text()
    if license_text:
        code = "/*\n" + license_text.replace("*/", "* /") + "\n*/\n" + code
    return "<script>" + code.replace("</script", "<\\/script") + "</script>"


def render_graph(
    view: GraphView, index: int, render_table: Callable[[Table], str]
) -> str:
    ident = f"rk-graph-{index}"
    data = asdict(view)
    # Tables are escaped and rendered once in HTML; JS only switches visibility.
    for frame in data["frames"]:
        frame.pop("table")
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    tables = ""
    if any(frame.table is not None for frame in view.frames):
        panels = []
        for frame_index, frame in enumerate(view.frames):
            content = (
                render_table(frame.table)
                if frame.table is not None and frame.table.rows
                else "<p>No observations in this view.</p>"
            )
            panels.append(
                f'<div data-graph-frame="{frame_index}"'
                f"{'' if frame_index == 0 else ' hidden'}>{content}</div>"
            )
        tables = (
            '<details class="graph-table"><summary>'
            + html.escape(view.table_title)
            + "</summary>"
            + "".join(panels)
            + "</details>"
        )
    return (
        f'<div id="{ident}" class="graph-view"><b>{html.escape(view.title)}</b> '
        '<select class="graph-scope" aria-label="统计范围"></select> <select class="graph-frame" aria-label="Graph view"></select> '
        ' <details class="graph-filter graph-filter-control" hidden style="position:relative;vertical-align:top;margin:0"><summary aria-label="Service 多选过滤"></summary>'
        '<div style="position:absolute;z-index:4;top:100%;left:0;width:max-content;max-width:80vw;background:#fff;border:1px solid #94a3b8;padding:8px;border-radius:4px">'
        '<div class="graph-filter-options" style="padding:8px;max-height:260px;overflow:auto"></div>'
        '<button type="button" class="graph-filter-all">全部</button></div></details>'
        ' <details class="graph-importance-filter graph-filter-control" hidden style="position:relative;vertical-align:top;margin:0"><summary aria-label="Importance 多选过滤"></summary>'
        '<div style="position:absolute;z-index:4;top:100%;left:0;width:max-content;max-width:80vw;background:#fff;border:1px solid #94a3b8;padding:8px;border-radius:4px">'
        '<div class="graph-filter-options" style="padding:8px;max-height:260px;overflow:auto"></div>'
        '<button type="button" class="graph-filter-all">全部</button></div></details>'
        ' <details class="graph-comparison-filter graph-filter-control" hidden style="position:relative;vertical-align:top;margin:0"><summary aria-label="数值多选过滤"></summary>'
        '<div style="position:absolute;z-index:4;top:100%;left:0;width:max-content;max-width:80vw;background:#fff;border:1px solid #94a3b8;padding:8px;border-radius:4px">'
        '<div class="graph-filter-options" style="padding:8px;max-height:260px;overflow:auto"></div>'
        '<button type="button" class="graph-filter-all">全部</button></div></details>'
        ' <button type="button" class="graph-fit">Fit</button>'
        ' <button type="button" class="graph-copy">复制图片</button>'
        ' <span class="graph-filter-status" role="status" aria-live="polite"></span>'
        ' <span class="graph-comparison-status" role="status" aria-live="polite"></span>'
        ' <span class="graph-layout-status" role="status" aria-live="polite"></span>'
        ' <span class="graph-copy-status" role="status" aria-live="polite"></span>'
        '<div class="graph-viewport" style="position:relative"><div class="graph-canvas"></div>'
        '<div class="graph-tooltip" role="tooltip" hidden style="position:absolute;pointer-events:auto;white-space:pre-wrap;overflow-wrap:anywhere;max-width:min(560px,90%);max-height:65%;overflow:auto;background:#fff;border:1px solid #94a3b8;padding:8px;border-radius:4px;z-index:3"></div>'
        '<label class="graph-zoom">Zoom '
        '<input type="range" min="0" max="100" value="100" aria-label="Graph zoom" '
        'title="Zoom: 0 selects the minimum visible scale; 100 is actual size">'
        "<output>100%</output></label></div>"
        '<pre class="graph-details" style="white-space:pre-wrap">Select a node for details.</pre>'
        f'{tables}</div><script>rkGraph(document.getElementById("{ident}"),{payload});</script>'
    )


def graph_scripts() -> str:
    return (
        asset_script("cytoscape.min.js")
        + asset_script("elk.bundled.js")
        + asset_script("graph-layout.js")
        + asset_script("graph-filter.js")
        + asset_script("graph-view.js")
    )
