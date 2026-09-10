"""Async Jaeger-on-OpenSearch adapter with projected tags and a shared HTTP pool."""

from __future__ import annotations

import hashlib
import ssl
from collections.abc import AsyncIterator

import httpx

from trace_harness.ingest.sources.base import SpanQuery
from trace_harness.ingest.sources.jaeger_file import normalize_es_doc
from trace_harness.loading.model import EvidenceRef
from trace_harness.model.span import NormSpan

BASE_FIELDS = (
    "traceID",
    "spanID",
    "operationName",
    "references",
    "startTime",
    "startTimeMillis",
    "duration",
    "process",
    "processID",
    "flags",
    "logs",
)


def _nested_eq(key: str, value: str) -> dict:
    return {
        "nested": {
            "path": "tags",
            "query": {
                "bool": {"filter": [{"term": {"tags.key": key}}, {"term": {"tags.value": value}}]}
            },
        }
    }


def selection_query(query: SpanQuery) -> dict:
    filters = [_nested_eq(k, v) for k, v in query.attr_eq.items()]
    if query.trace_ids is not None:
        filters.append({"terms": {"traceID": query.trace_ids}})
    if query.service:
        filters.append({"term": {"process.serviceName": query.service}})
    bounds = {}
    if query.since_ms is not None:
        bounds["gte"] = query.since_ms
    if query.until_ms is not None:
        bounds["lte"] = query.until_ms
    if bounds:
        filters.append({"range": {"startTimeMillis": bounds}})
    if query.error_only:
        filters.append(
            {
                "bool": {
                    "should": [
                        _nested_eq("error", "true"),
                        _nested_eq("otel.status_code", "ERROR"),
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return {"bool": {"filter": filters}}


class OpenSearchSource:
    def __init__(
        self,
        base_url: str,
        index: str,
        *,
        headers: dict[str, str] | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 60,
        page_size: int = 500,
        concurrency: int = 8,
        verify: bool | ssl.SSLContext = True,
        transport: httpx.AsyncBaseTransport | None = None,
        namespace: str = "",
    ):
        self.base_url, self.index = base_url.rstrip("/"), index
        self.page_size = page_size
        self.namespace = hashlib.sha256(f"{namespace}|{self.base_url}|{index}".encode()).hexdigest()
        self.client = httpx.AsyncClient(
            headers=headers,
            auth=(username, password or "") if username else None,
            timeout=timeout,
            verify=verify,
            transport=transport,
            limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
        )
        self.requests = self.bytes_read = 0

    async def _search(self, payload: dict) -> dict:
        response = await self.client.post(f"{self.base_url}/{self.index}/_search", json=payload)
        response.raise_for_status()
        self.requests += 1
        self.bytes_read += len(response.content)
        result = response.json()
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("OpenSearch returned incomplete search results")
        return result

    async def select(self, query: SpanQuery) -> AsyncIterator[str]:
        # Composite aggregation paginates distinct traces, so a large trace cannot consume
        # the selection limit and silently hide all the other members.
        after = None
        count = 0
        while count < query.limit:
            composite = {
                "size": min(self.page_size, query.limit - count),
                "sources": [{"trace": {"terms": {"field": "traceID"}}}],
            }
            if after is not None:
                composite["after"] = after
            result = await self._search(
                {
                    "size": 0,
                    "query": selection_query(query),
                    "aggs": {"members": {"composite": composite}},
                }
            )
            group = result["aggregations"]["members"]
            for bucket in group["buckets"]:
                yield str(bucket["key"]["trace"])
                count += 1
            next_after = group.get("after_key")
            if not group["buckets"] or next_after is None:
                return
            if next_after == after:
                raise RuntimeError("OpenSearch selection cursor did not advance")
            after = next_after

    async def _documents(self, query: dict, fields: tuple[str, ...] | None) -> dict[str, NormSpan]:
        out = {}
        after = None
        while True:
            q = {"bool": {"filter": [query]}}
            payload = {
                "size": self.page_size,
                "sort": [{"startTimeMillis": "asc"}, {"spanID": "asc"}],
                "query": q,
            }
            if fields is not None:
                payload["_source"] = list(BASE_FIELDS)
                if fields:
                    q["bool"].update(
                        should=[
                            {
                                "nested": {
                                    "path": "tags",
                                    "score_mode": "none",
                                    "query": {"terms": {"tags.key": list(fields)}},
                                    "inner_hits": {"name": "fields", "size": 100, "_source": True},
                                }
                            }
                        ],
                        minimum_should_match=0,
                    )
            if after is not None:
                payload["search_after"] = after
            result = await self._search(payload)
            hits = result.get("hits", {}).get("hits", [])
            for hit in hits:
                doc = hit["_source"]
                if fields is not None:
                    inner = hit.get("inner_hits", {}).get("fields", {}).get("hits", {})
                    nested = inner.get("hits", [])
                    total = inner.get("total", 0)
                    if isinstance(total, dict):
                        total = total["value"]
                    if total > len(nested):
                        raise RuntimeError(
                            "OpenSearch tag projection truncated; narrow the field request"
                        )
                    doc = {**doc, "tags": [item["_source"] for item in nested]}
                span = normalize_es_doc(doc)
                if span:
                    span.loaded_fields = fields
                    span.storage_index = hit.get("_index", "")
                    span.storage_id = hit.get("_id", "")
                    if span.span_id in out:
                        raise ValueError(f"duplicate physical span identity: {span.span_id}")
                    out[span.span_id] = span
            if len(hits) < self.page_size:
                return out
            cursor = hits[-1].get("sort")
            if cursor is None or cursor == after:
                raise RuntimeError("OpenSearch span cursor did not advance")
            after = cursor

    async def fetch(self, trace_id: str, fields: tuple[str, ...]) -> dict[str, NormSpan]:
        return await self._documents({"term": {"traceID": trace_id}}, fields)

    async def read(
        self, refs: tuple[EvidenceRef, ...], fields: tuple[str, ...] | None
    ) -> dict[str, NormSpan]:
        if not refs:
            return {}
        if len({r.trace_id for r in refs}) != 1:
            raise ValueError("one read batch must belong to one trace")
        alternatives = []
        for ref in refs:
            terms = [{"term": {"traceID": ref.trace_id}}, {"term": {"spanID": ref.span_id}}]
            if ref.document_id:
                terms.extend(
                    ({"term": {"_index": ref.index}}, {"ids": {"values": [ref.document_id]}})
                )
            alternatives.append({"bool": {"filter": terms}})
        return await self._documents(
            {"bool": {"should": alternatives, "minimum_should_match": 1}}, fields
        )

    async def aclose(self) -> None:
        await self.client.aclose()
