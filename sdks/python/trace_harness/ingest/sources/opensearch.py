"""OpenSearchSource —— 按 trace_id / 条件查 Jaeger-on-OpenSearch 索引的通用 Source。

纯通用参数版（base_url / index / 认证全是入参），**零业务知识**——env 注册表、kubevpn、
凭据解析是 infra 知识，留消费方（trace-as）装配后注入。任何 Jaeger-on-OpenSearch 用户
开箱可用。

实现要点（对齐 jaeger ES 存储特例）：
- tags 是 **nested** 类型，attr 等值/error 过滤必须用 `nested` query，普通 term 命不中。
- `_search` 默认只回 10 条，拿全量靠 search_after 翻页（按 startTimeMillis+spanID 排序）。
- select 投影后在 source 层 post-strip 巨型 attr（jaeger ES 无法按单个 nested tag key
  做 _source 裁剪，故传输边界靠 limit + CPU 侧剥离；真正的 body 走 raw_attrs 懒拉）。
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

from trace_harness.ingest.sources.base import Fidelity, SpanQuery, strip_attrs
from trace_harness.ingest.sources.jaeger_file import normalize_es_doc
from trace_harness.model.span import NormSpan

# 误差容忍：error 信号在 jaeger ES 里的两种存法
_ERROR_OR = [
    {
        "nested": {
            "path": "tags",
            "query": {
                "bool": {
                    "must": [
                        {"term": {"tags.key": "otel.status_code"}},
                        {"term": {"tags.value": "ERROR"}},
                    ]
                }
            },
        }
    },
    {
        "nested": {
            "path": "tags",
            "query": {
                "bool": {
                    "must": [{"term": {"tags.key": "error"}}, {"term": {"tags.value": "true"}}]
                }
            },
        }
    },
]


def _nested_eq(key: str, value: str) -> dict:
    return {
        "nested": {
            "path": "tags",
            "query": {
                "bool": {"must": [{"term": {"tags.key": key}}, {"term": {"tags.value": value}}]}
            },
        }
    }


def _default_urlopen(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


class OpenSearchSource:
    """Jaeger-on-OpenSearch Source。

    Args:
        base_url: 如 ``http://10.255.3.164:9200``。
        index: 索引表达式，如 ``jaeger-span-*``。
        headers: 完整请求头（含 Authorization）；或给 username/password 走 basic-auth。
        urlopen: 可注入的 ``(request, timeout) -> response``，便于消费方挂 SSL skip-verify。
    """

    def __init__(
        self,
        base_url: str,
        index: str,
        *,
        headers: dict[str, str] | None = None,
        username: str | None = None,
        password: str | None = None,
        urlopen: Callable | None = None,
        timeout: float = 60.0,
        page_size: int = 1000,
    ):
        self.base_url = base_url.rstrip("/")
        self.index = index
        self.timeout = timeout
        self.page_size = page_size
        self._urlopen = urlopen or _default_urlopen
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", "application/json")
        if username and password and "Authorization" not in self.headers:
            import base64

            tok = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            self.headers["Authorization"] = f"Basic {tok}"

    # —— HTTP ——
    def _search(self, payload: dict) -> dict:
        url = f"{self.base_url}/{self.index}/_search"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=self.headers, method="GET"
        )
        with self._urlopen(req, self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _paginate(self, query: dict, limit: int) -> list[dict]:
        out: list[dict] = []
        after = None
        while len(out) < limit:
            payload = {
                "size": min(self.page_size, limit - len(out)),
                "sort": [{"startTimeMillis": {"order": "asc"}}, {"spanID": {"order": "asc"}}],
                "query": query,
            }
            if after is not None:
                payload["search_after"] = after
            hits = self._search(payload).get("hits", {}).get("hits", [])
            if not hits:
                break
            out.extend(h.get("_source", {}) for h in hits)
            after = hits[-1].get("sort")
            if len(hits) < payload["size"]:
                break
        return out

    # —— Source 协议 ——
    def select(self, query: SpanQuery) -> list[NormSpan]:
        must: list[dict] = []
        for k, v in query.attr_eq.items():
            must.append(_nested_eq(k, v))
        if query.trace_ids:
            must.append({"terms": {"traceID": query.trace_ids}})
        if query.service:
            must.append({"term": {"process.serviceName": query.service}})
        rng: dict = {}
        if query.since_ms:
            rng["gte"] = query.since_ms
        if query.until_ms:
            rng["lte"] = query.until_ms
        if rng:
            must.append({"range": {"startTimeMillis": rng}})
        bool_q: dict = {"must": must}
        if query.error_only:
            bool_q["should"] = _ERROR_OR
            bool_q["minimum_should_match"] = 1
        docs = self._paginate(
            {"bool": bool_q} if (must or query.error_only) else {"match_all": {}}, query.limit
        )
        spans = [normalize_es_doc(d) for d in docs]
        return [strip_attrs(s, query.drop_attrs) for s in spans if s]

    def fetch(self, trace_id: str, fidelity: Fidelity = Fidelity.LIGHT) -> dict[str, NormSpan]:
        docs = self._paginate({"term": {"traceID": trace_id}}, limit=100000)
        drop = () if fidelity == Fidelity.FULL else SpanQuery().drop_attrs
        out: dict[str, NormSpan] = {}
        for d in docs:
            s = normalize_es_doc(d)
            if s:
                out[s.span_id] = strip_attrs(s, drop) if drop else s
        return out

    def raw_attrs(self, trace_id: str, span_id: str) -> dict:
        docs = self._paginate({"term": {"spanID": span_id}}, limit=1)
        s = normalize_es_doc(docs[0]) if docs else None
        return s.attrs if s else {}
