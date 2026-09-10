"""Latest membership is based on matching span time, not ID or span count."""

import json

import httpx
import pytest

from trace_harness.ingest.sources.base import SpanQuery
from trace_harness.ingest.sources.jaeger_file import JaegerFileSource
from trace_harness.ingest.sources.opensearch import OpenSearchSource


async def test_latest_opensearch_pages_duplicates_before_limit():
    payloads = []
    pages = [
        [("z", 30, "a"), ("z", 29, "b")],
        [("b", 20, "c"), ("b", 19, "d")],
        [("a", 10, "e")],
    ]

    def respond(request):
        body = json.loads(request.content)
        payloads.append(body)
        hits = [
            {"_source": {"traceID": tid}, "sort": [start, tid, sid, "day"]}
            for tid, start, sid in pages[len(payloads) - 1]
        ]
        return httpx.Response(200, json={"hits": {"hits": hits}})

    source = OpenSearchSource(
        "https://test.invalid", "day-*", page_size=2, transport=httpx.MockTransport(respond)
    )
    try:
        query = SpanQuery(order="latest", limit=3, operation_names=["request"], until_ms=50)
        assert [tid async for tid in source.select(query)] == ["z", "b", "a"]
        assert len(payloads) == 3
        assert payloads[1]["search_after"] == [29, "z", "b", "day"]
        assert payloads[0]["_source"] == ["traceID"]
        assert {"terms": {"operationName": ["request"]}} in payloads[0]["query"]["bool"]["filter"]
    finally:
        await source.aclose()


async def test_latest_file_filters_before_order_and_limit(tmp_path):
    docs = []
    for tid, operation, start in [
        ("old", "request", 1),
        ("old", "later-work", 90),
        ("new", "request", 30),
        ("same", "request", 30),
        ("future", "request", 200),
        ("mid", "request", 20),
    ]:
        docs.append(
            {
                "traceID": tid,
                "spanID": f"{tid}-{operation}",
                "operationName": operation,
                "startTime": start * 1000,
                "duration": 1000,
            }
        )
    path = tmp_path / "input.jsonl"
    path.write_text("\n".join(json.dumps(d) for d in reversed(docs)))
    source = JaegerFileSource(path)
    try:
        query = SpanQuery(order="latest", operation_names=["request"], until_ms=100, limit=2)
        assert [tid async for tid in source.select(query)] == ["new", "same"]
        query.order = "trace_id"
        assert [tid async for tid in source.select(query)] == ["mid", "new"]
    finally:
        await source.aclose()


async def test_latest_cursor_failure_is_visible():
    source = OpenSearchSource(
        "https://test.invalid",
        "day-*",
        page_size=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [{"_source": {"traceID": "one"}, "sort": [1, "one", "s", "day"]}]
                    }
                },
            )
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="cursor"):
            [tid async for tid in source.select(SpanQuery(order="latest", limit=2))]
    finally:
        await source.aclose()
