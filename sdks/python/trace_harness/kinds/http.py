"""普通 HTTP 的单次慢请求与连续串行调用判读。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from trace_harness.model.context import TraceContext
from trace_harness.model.span import NormSpan

LLM_URL_MARKS = ("/chat/completions", "/embeddings", "/rerank")

_HTTP_NAME = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)(?:\s+(\S+))?$")


def _object(value) -> dict:
    for _ in range(3):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _endpoint(span: NormSpan) -> tuple[str, str, str] | None:
    if span.attr("asgi.event.type"):
        return None
    match = _HTTP_NAME.fullmatch(span.name)
    method = span.attr("http.request.method", "http.method")
    if method is None and match:
        method = match[1]
    if method is None:
        return None
    url = span.attr(
        "url.full",
        "http.url",
        "http.target",
        "url.path",
    )
    url = str(url or (match[2] if match else "") or "")
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    route = str(span.attr("http.route") or parts.path)
    if not route:
        return None
    # Action 风格 RPC 的路径相同但 API 不同；普通分页等参数不参与 API 身份。
    action = parse_qs(parts.query).get("Action")
    if action:
        route += f"?Action={action[0]}"
    target = parts.netloc.rsplit("@", 1)[-1] or str(
        span.attr("server.address", "net.peer.name") or span.service or "?"
    )
    if not parts.netloc and (port := span.attr("server.port", "net.peer.port")) is not None:
        target = f"{target}:{port}"
    return str(method).upper(), target, route


def _is_stream(span: NormSpan) -> bool:
    for key in (
        "http.request.header.accept",
        "http.response.header.content-type",
        "http.response.header.content_type",
    ):
        if "text/event-stream" in str(span.attr(key, default="")).lower():
            return True
    for key in (
        "http.request.headers",
        "http.response.headers",
    ):
        headers = _object(span.attr(key))
        if any(
            str(k).lower() in {"accept", "content-type"} and "text/event-stream" in str(v).lower()
            for k, v in headers.items()
        ):
            return True
    for key in (
        "http.request.body",
        "http.request.body.json",
    ):
        if _object(span.attr(key)).get("stream") is True:
            return True
    return False


@dataclass
class HttpRequest:
    span: NormSpan
    spans: tuple[NormSpan, ...]
    api: tuple[str, str, str]
    ordinary: bool

    @property
    def timing_span(self) -> NormSpan:
        # 单次告警检查完整观测；串行起止始终使用调用方 span，避免混用服务端时钟。
        return max(self.spans, key=lambda span: span.dur_ms)

    @property
    def duration_ms(self) -> float:
        # 有些客户端 span 只覆盖到响应头；服务端 span 存在时也检查完整处理耗时。
        return self.timing_span.dur_ms

    @property
    def start_ms(self) -> float:
        return self.span.start_ms

    @property
    def end_ms(self) -> float:
        return self.span.end_ms

    @property
    def label(self) -> str:
        method, target, route = self.api
        return f"{method} {target}{route}"


def http_requests(ctx: TraceContext) -> list[HttpRequest]:
    endpoints = {
        span.span_id: endpoint for span in ctx.spans.values() if (endpoint := _endpoint(span))
    }
    peers: dict[str, list[NormSpan]] = defaultdict(list)
    paired: set[str] = set()
    children: dict[str, list[NormSpan]] = defaultdict(list)
    for span in ctx.spans.values():
        children[span.parent_span_id].append(span)
        parent = ctx.spans.get(span.parent_span_id)
        if (
            span.span_id in endpoints
            and parent is not None
            and parent.span_id in endpoints
            and span.attr("span.kind") == "server"
            and parent.attr("span.kind") == "client"
        ):
            peers[parent.span_id].append(span)
            paired.add(span.span_id)

    requests = []
    for sid, endpoint in endpoints.items():
        if sid in paired:
            continue
        span = ctx.spans[sid]
        spans = (span, *peers[sid])
        method, target, route = endpoint
        if peers[sid]:
            route = endpoints[peers[sid][0].span_id][2]
        evidence = [
            *spans,
            *(
                item
                for member in spans
                for item in children[member.span_id]
                if item.name in {"request-body", "response-body"}
            ),
        ]
        ordinary = not any(_is_stream(item) for item in evidence)
        ordinary = ordinary and not any(mark in route for mark in LLM_URL_MARKS)
        parent = span
        seen: set[str] = set()
        while ordinary and parent is not None and parent.span_id not in seen:
            seen.add(parent.span_id)
            if (owner := ctx.view().by_span.get(parent.span_id)) and owner.kind == "model-call":
                ordinary = False
                break
            parent = ctx.spans.get(parent.parent_span_id)
        requests.append(HttpRequest(span, spans, (method, target, route), ordinary))
    return requests
