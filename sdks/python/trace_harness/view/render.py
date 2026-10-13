"""One public rendering verb over a serializable, already-prepared View."""

import html
import json
from typing import Literal

from trace_harness.view.interactive import render_trace_html


def _text(view: dict) -> str:
    lines = [f"trace={view['trace_id']} {view['scope']} view={view['view']}"]
    if view["scope"] == "trace":
        rows = view["rows"]
        lines.append(
            f"status={view['status']}; {len(rows['items'])}/{rows['total']} rows; "
            f"offset={rows['offset']}; next_offset={rows['next_offset']}"
        )
        for row in rows["items"]:
            lines.append(
                "  "
                * row["depth"]
                + f"{row['kind']} {json.dumps(row['name'], ensure_ascii=False)} "
                f"duration_ms={row['duration_ms']} id={json.dumps(row['node_id'])} "
                f"parent={json.dumps(row.get('parent_id'))} "
                f"nodes={json.dumps(row['source_node_ids'])} spans={json.dumps(row['span_ids'])} "
                f"findings={json.dumps(row.get('findings', []), ensure_ascii=False)}"
            )
        lines.extend(json.dumps(m, ensure_ascii=False) for m in view["measurements"])
    else:
        lines.append(f"node={view['node']['node_id']} duration_ms={view['node']['duration_ms']}")
        lines.append(json.dumps(view, ensure_ascii=False, indent=2))
    return "\n".join(lines) + "\n"


def _html(view: dict) -> str:
    if view["scope"] == "trace":
        return render_trace_html([view])
    body = f"<h1>{html.escape(view['node']['name'])} · {view['view']}</h1>"
    if view["view"] == "timeline" and view["calls"] and view["timing"]["window"]:
        duration = max(view["timing"]["window"]["elapsed_ms"], 0.001)
        body += "<p>请求开始 → 节点结束；嵌套与并行调用重叠，累计耗时不代表独占等待贡献。</p>"
        for call in view["calls"]["items"]:
            x = call["start"] / duration * 1000
            width = max(1, call["duration"] / duration * 1000)
            body += (
                f"<div>{html.escape(call['name'])} · {call['duration']}ms"
                '<svg viewBox="0 0 1000 20" role="img" aria-label="调用时间线">'
                f'<rect x="{x}" y="3" width="{width}" height="14" fill="#3b82f6"/>'
                "</svg></div>"
            )
    body += "<pre>" + html.escape(json.dumps(view, ensure_ascii=False, indent=2)) + "</pre>"
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        "body{font:14px system-ui;margin:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "svg{display:block;width:100%;height:20px}</style></head><body>" + body + "</body></html>"
    )


def render(view: dict, *, format: Literal["text", "html", "json"]) -> str:
    if format == "text":
        return _text(view)
    if format == "html":
        return _html(view)
    if format == "json":
        return json.dumps(view, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    raise ValueError(f"Unsupported view format: {format}")
