"""Offline Cytoscape renderer; inputs are already reduced, domain-neutral view data."""

import html
import json
from dataclasses import asdict
from importlib.resources import files

from harness_common.report_kit.doc import GraphView


def asset_script(name: str) -> str:
    # Inline scripts must not contain an HTML closing-script delimiter.
    assets = files("harness_common.report_kit").joinpath("assets")
    notices = {
        "cytoscape.min.js": ("cytoscape-LICENSE",),
        "cytoscape-dagre.js": (
            "cytoscape-dagre-LICENSE",
            "dagre-LICENSE",
            "graphlib-LICENSE",
        ),
        "echarts.min.js": ("echarts-LICENSE", "echarts-NOTICE"),
    }
    license_text = "\n".join(
        assets.joinpath(item).read_text() for item in notices.get(name, ())
    )
    code = assets.joinpath(name).read_text()
    if license_text:
        code = "/*\n" + license_text.replace("*/", "* /") + "\n*/\n" + code
    return "<script>" + code.replace("</script", "<\\/script") + "</script>"


def render_graph(view: GraphView, index: int) -> str:
    ident = f"rk-graph-{index}"
    payload = json.dumps(asdict(view), ensure_ascii=False).replace("<", "\\u003c")
    return (
        f'<div id="{ident}" class="graph-view"><b>{html.escape(view.title)}</b> '
        '<select aria-label="Graph view"></select> <button type="button">Fit</button>'
        '<div class="graph-canvas" style="height:600px;border:1px solid #d0d7de"></div>'
        '<pre class="graph-details" style="white-space:pre-wrap">Select a node for details.</pre>'
        f'</div><script>rkGraph(document.getElementById("{ident}"),{payload});</script>'
    )


def graph_scripts() -> str:
    return (
        asset_script("cytoscape.min.js")
        + asset_script("cytoscape-dagre.js")
        + asset_script("graph-view.js")
    )
