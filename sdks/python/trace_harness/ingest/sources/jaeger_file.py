"""JaegerFileSource —— 直吃两种 Jaeger 落盘格式，自动嗅探：

1. **ES jsonl**（OpenSearch `jaeger-span-*` 索引的 `_source` 逐行）：spanID /
   references(CHILD_OF→parent) / startTime,duration(μs) / tags[{key,type,value}] /
   process.serviceName / logs(异常事件)。
2. **Jaeger UI 导出 JSON**（`{"data":[{traceID, spans, processes}]}`）：span 结构同上，
   但 service 不内嵌——span 带 `processID`，trace 级 `processes` 表给映射；加载时解引用
   回填成 ES 形态，下游单一格式。

归一只做 kind-无关的骨架抽取——**不算语义 kind**（那是 spec.matches 的事）。
"""

from __future__ import annotations

import json
from pathlib import Path

from trace_harness.model.span import NormSpan

_CHILD_OF = "CHILD_OF"


def _flatten_tags(doc: dict) -> dict:
    """tags: [{key,type,value}] → {key: value}。"""
    return {t.get("key"): t.get("value") for t in doc.get("tags", []) if t.get("key")}


def _parent_of(doc: dict) -> str | None:
    for ref in doc.get("references", []):
        if ref.get("refType", _CHILD_OF) == _CHILD_OF and ref.get("spanID"):
            return ref["spanID"]
    return None


def _events_info(doc: dict, attrs: dict) -> tuple[bool, list, list]:
    """一次遍历 logs：所有 span event 归一进 ``events``；异常事件再抽进 ``error_events``。

    has_error：otel.status_code==ERROR / error==true / 出现异常事件，任一即真。
    """
    has_error = str(attrs.get("otel.status_code") or "").upper() == "ERROR"
    if attrs.get("error") in (True, "true", "True"):
        has_error = True
    error_events: list = []
    events: list = []
    for log in doc.get("logs", []):
        fields = {f.get("key"): f.get("value") for f in log.get("fields", [])}
        name = fields.get("event", "")
        ts = log.get("timestamp")  # jaeger log timestamp 同 startTime 是 μs
        events.append(
            {
                "name": name,
                # 缺 timestamp 保留 None，避免和真 epoch-0 混淆；有则 μs→ms
                "timestamp_ms": ts / 1000 if ts is not None else None,
                "attrs": {k: v for k, v in fields.items() if k != "event"},
            }
        )
        if name == "exception" or fields.get("exception.type"):
            error_events.append(
                {
                    "type": fields.get("exception.type", ""),
                    "message": fields.get("exception.message", ""),
                    "stacktrace": fields.get("exception.stacktrace", ""),
                }
            )
            has_error = True
    return has_error, error_events, events


def normalize_es_doc(doc: dict) -> NormSpan | None:
    """ES jaeger-span `_source`（文件行 / OpenSearch hit 同形）→ NormSpan。

    单一归一口径：JaegerFileSource 与 OpenSearchSource 都走这里，下游只见 NormSpan。
    只做 kind-无关骨架抽取，语义 kind 是 spec.matches 的事。
    """
    sid = doc.get("spanID")
    if not sid:
        return None
    attrs = _flatten_tags(doc)
    has_error, error_events, events = _events_info(doc, attrs)
    return NormSpan(
        span_id=sid,
        parent_span_id=_parent_of(doc),
        name=doc.get("operationName") or "?",
        # jaeger startTime/duration 是 μs；时间统一 ms，换算收口在 source 层
        start_ms=int(doc.get("startTime") or 0) / 1000,
        dur_ms=int(doc.get("duration") or 0) / 1000,
        service=(doc.get("process") or {}).get("serviceName"),
        has_error=has_error,
        attrs=attrs,
        raw=doc,
        error_events=error_events,
        events=events,
    )


# 历史内部别名
_norm = normalize_es_doc


def _load_ui_export(doc: dict) -> dict[str, NormSpan]:
    """Jaeger UI 导出 → NormSpan 集：processID 经 trace 级 processes 表解引用成 service。"""
    out: dict[str, NormSpan] = {}
    for trace in doc.get("data") or []:
        processes = trace.get("processes") or {}
        for sp in trace.get("spans") or []:
            proc = processes.get(sp.get("processID") or "", {})
            span = _norm({**sp, "process": proc})
            if span:
                out[span.span_id] = span
    return out


def load_jaeger_file(path: str | Path) -> dict[str, NormSpan]:
    """ES jsonl 或 Jaeger UI 导出 JSON → {span_id: NormSpan}。三种形态自动嗅探：

    1. UI 导出信封：``{"data": [{traceID, spans, processes}], ...}``
    2. **根级单条 trace**：``{traceID, spans, processes}``——信封被剥（Jaeger 某些下载路径、
       或手抠了 data 内层），根上直接是一条 trace；按单条 UI trace 处理。
    3. ES jsonl：每行一个 span ``_source``。

    嗅探开销：整文 parse 成 dict 即 1/2（UI 是一个多行 JSON，按行 parse 必败）；ES jsonl
    整文 parse 在第二行即 Extra data 失败，开销只有首个对象。
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            if isinstance(doc.get("data"), list):
                return _load_ui_export(doc)
            # 根级单条 trace（信封被剥）：根上同时有 traceID + spans 列表 → 包回信封复用同一路径
            if doc.get("traceID") and isinstance(doc.get("spans"), list):
                return _load_ui_export({"data": [doc]})
    except ValueError:
        pass
    out: dict[str, NormSpan] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        span = _norm(json.loads(line))
        if span:
            out[span.span_id] = span
    return out


class JaegerFileSource:
    """离线文件 Source：一个 .jsonl（一条 trace）在内存里做 select/fetch。

    select 在已加载的 span 上做内存过滤（attr_eq / error_only / service / 时间窗），
    fetch 直接返回该 trace 全部 span。多文件场景：传 dir，按 traceID 索引。
    """

    def __init__(self, path):
        from pathlib import Path

        p = Path(path)
        files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
        self._by_trace: dict[str, dict[str, NormSpan]] = {}
        for f in files:
            spans = load_jaeger_file(f)
            for s in spans.values():
                tid = str(s.raw.get("traceID", "?"))
                self._by_trace.setdefault(tid, {})[s.span_id] = s

    def _all(self):
        for spans in self._by_trace.values():
            yield from spans.values()

    def select(self, query) -> list[NormSpan]:
        hits = []
        for s in self._all():
            if query.trace_ids and str(s.raw.get("traceID")) not in query.trace_ids:
                continue
            if query.error_only and not s.has_error:
                continue
            if query.service and s.service != query.service:
                continue
            if query.since_ms and s.start_ms < query.since_ms:
                continue
            if query.until_ms and s.start_ms > query.until_ms:
                continue
            if any(str(s.attrs.get(k)) != v for k, v in query.attr_eq.items()):
                continue
            hits.append(s)
            if len(hits) >= query.limit:
                break
        return hits

    def fetch(self, trace_id: str, fidelity=None) -> dict[str, NormSpan]:
        return dict(self._by_trace.get(trace_id, {}))

    def raw_attrs(self, trace_id: str, span_id: str) -> dict:
        s = self._by_trace.get(trace_id, {}).get(span_id)
        return s.attrs if s else {}
