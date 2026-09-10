"""End-to-end contracts for projected evidence, cache reuse and dataset execution."""

import asyncio
import json

import pytest

from trace_harness import (
    Dataset,
    EvidenceDependency,
    FactDependency,
    FactProducer,
    Finding,
    LoadConfig,
    TraceContributions,
    TraceHarness,
)
from trace_harness.ingest.sources.jaeger_file import normalize_es_doc
from trace_harness.kinds import genai
from trace_harness.loading.model import EvidenceMissing


class MemorySource:
    namespace = "test-source"

    def __init__(self, count=2):
        self.docs = {
            f"t{i}": {
                "traceID": f"t{i}",
                "spanID": "s",
                "operationName": "chat",
                "startTime": 1000,
                "duration": 500000 + i * 1000,
                "tags": [
                    {"key": "gen_ai.operation.name", "value": "chat"},
                    {"key": "payload", "value": "x" * 10000},
                ],
            }
            for i in range(count)
        }
        self.reads = 0
        self.fetches = 0
        self.closed = False

    async def select(self, query):
        for tid in sorted(self.docs)[: query.limit]:
            if query.trace_ids is None or tid in query.trace_ids:
                yield tid

    def project(self, doc, fields):
        return normalize_es_doc(
            {**doc, "tags": [t for t in doc["tags"] if fields is None or t["key"] in fields]}
        )

    async def fetch(self, tid, fields):
        self.fetches += 1
        return {"s": self.project(self.docs[tid], fields)}

    async def read(self, refs, fields):
        self.reads += 1
        await asyncio.sleep(0)
        return {
            r.span_id: self.project(self.docs[r.trace_id], fields)
            for r in refs
            if r.trace_id in self.docs
        }

    async def aclose(self):
        self.closed = True


def harness(**kwargs):
    producer = FactProducer(
        ("size",),
        lambda n: n.kind == "model-call",
        lambda n, t: (EvidenceDependency((n.primary_span_id,), ("payload",)),),
        lambda n, t: {"size": len(t.raw_attr(n.primary_span_id)["payload"])},
    )
    return TraceHarness(
        TraceContributions(specs=tuple(genai.specs()), fact_producers=(producer,), **kwargs)
    )


async def test_projection_and_coalesced_facts(tmp_path):
    src = MemorySource()
    async with harness().open(src, work_dir=tmp_path) as session:
        dataset = await session.select()
        assert src.fetches == src.reads == 0
        async with session.tree(dataset, "t0") as ctx:
            node = ctx.trace.nodes[0]
            assert "payload" not in ctx.trace.spans["s"].attrs
            assert "x" * 10000 not in json.dumps(ctx.trace.spans["s"].raw)
            assert await asyncio.gather(*(ctx.fact(node, "size") for _ in range(5))) == [10000] * 5
            assert src.reads == 1
            assert (await ctx.measure(node, "self_ms")).values == {"self_ms": 500}
        with pytest.raises(RuntimeError, match="lease"):
            await ctx.fact(node, "size")
    assert src.closed


async def test_disk_reuse_and_source_isolation(tmp_path):
    async with harness().open(MemorySource(), work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            await ctx.fact(ctx.trace.nodes[0], "size")
    src = MemorySource()
    async with harness().open(src, work_dir=tmp_path) as session:
        async with session.tree(Dataset.load(dataset.path), "t0") as ctx:
            assert await ctx.fact(ctx.trace.nodes[0], "size") == 10000
        assert src.reads == src.fetches == 0
        src.namespace = "other"
        with pytest.raises(ValueError, match="different source"):
            async with session.tree(dataset, "t0"):
                pass


async def test_lazy_and_eager_equal(tmp_path):
    results = []
    for lazy in (True, False):
        async with harness().open(
            MemorySource(), work_dir=tmp_path / str(lazy), config=LoadConfig(lazy=lazy)
        ) as session:
            dataset = await session.select()
            async with session.tree(dataset, "t0") as ctx:
                node = ctx.trace.nodes[0]
                size = await ctx.fact(node, "size")
                result = await session.analyze(ctx)
                await session.prepare_view(result, full=True)
                results.append(
                    (
                        size,
                        result.measurements,
                        session.harness.render_interactive(
                            result.trace, result.findings, measurements=result.measurements
                        ),
                    )
                )
    assert results[0] == results[1]


async def test_missing_evidence_is_not_empty(tmp_path):
    src = MemorySource()
    async with harness().open(src, work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            del src.docs["t0"]
            with pytest.raises(EvidenceMissing):
                await ctx.fact(ctx.trace.nodes[0], "size")


async def test_cycle_fails_without_hanging(tmp_path):
    p = FactProducer(
        ("cycle",),
        lambda n: True,
        lambda n, t: (FactDependency(n.node_id, "cycle"),),
        lambda n, t: {},
    )
    h = TraceHarness(TraceContributions(specs=tuple(genai.specs()), fact_producers=(p,)))
    async with h.open(MemorySource(), work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            with pytest.raises(ValueError, match="cyclic"):
                await asyncio.wait_for(ctx.fact(ctx.trace.nodes[0], "cycle"), 1)


async def test_batch_detector_and_repeat_run(tmp_path):
    async def sizes(dataset, context):
        total = 0
        async for ctx in context.trees(dataset):
            total += await ctx.fact(ctx.trace.nodes[0], "size")
        return [Finding(None, "sizes", "info", scope="cohort", data={"total": total})]

    src = MemorySource(5)
    async with harness(batch_detectors=(sizes,)).open(src, work_dir=tmp_path) as session:
        dataset = await session.select()
        one = await session.detect(dataset, detectors=[], metrics=["self_ms"])
        two = await session.detect(dataset, detectors=[], metrics=[], batch_detectors=[])
        assert one.summary["coverage"] == {"selected": 5, "succeeded": 5, "failed": 0}
        assert next(one.rows("findings"))["data"]["total"] == 50000
        assert list(two.rows("findings")) == []
        assert list(two.rows("measurements")) == []
        assert src.reads == 5
        assert len(session._leases) == 0


async def test_temporary_workspace_cleanup():
    async with harness().open(MemorySource()) as session:
        dataset = await session.select()
        path = dataset.path
        assert path.exists()
    assert not path.exists()


async def test_field_states_and_concurrent_cycle(tmp_path):
    producer = FactProducer(
        ("a",),
        lambda n: True,
        lambda n, t: (FactDependency(n.node_id, "b"),),
        lambda n, t: {"a": 1},
    )
    other = FactProducer(
        ("b",),
        lambda n: True,
        lambda n, t: (FactDependency(n.node_id, "a"),),
        lambda n, t: {"b": 1},
    )
    h = TraceHarness(
        TraceContributions(specs=tuple(genai.specs()), fact_producers=(producer, other))
    )
    async with h.open(MemorySource(), work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            span = ctx.trace.spans["s"]
            assert span.field_state("payload") == "unloaded"
            await ctx.runtime.loader.load(ctx.trace, ("s",), ("absent",))
            assert span.field_state("absent") == "missing"
            results = await asyncio.wait_for(
                asyncio.gather(
                    ctx.fact(ctx.trace.nodes[0], "a"),
                    ctx.fact(ctx.trace.nodes[0], "b"),
                    return_exceptions=True,
                ),
                1,
            )
            assert all(isinstance(r, ValueError) for r in results)


async def test_streaming_header_projection_preserves_findings(tmp_path):
    src = MemorySource(1)
    src.docs["t0"]["operationName"] = "POST /chat"
    src.docs["t0"]["tags"] = [
        {"key": "http.method", "value": "POST"},
        {"key": "http.url", "value": "http://chat/chat"},
        {"key": "http.request.header.accept", "value": "text/event-stream"},
    ]
    async with harness().open(src, work_dir=tmp_path) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            result = await session.analyze(ctx)
            assert not any(
                f.source == "http_slow_request" for fs in result.findings.values() for f in fs
            )
            assert ctx.trace.spans["s"].field_state("http.request.header.accept") == "loaded"
            assert src.reads == 1


async def test_batch_partial_failure_and_exact_quantiles(tmp_path):
    src = MemorySource(5)
    async with harness().open(src, work_dir=tmp_path) as session:
        dataset = await session.select()
        del src.docs["t4"]
        result = await session.detect(dataset, detectors=[], metrics=["self_ms"])
        assert result.summary["coverage"]["failed"] == 1
        assert next(result.rows("failures"))["trace_id"] == "t4"
        row = next(r for r in result.summary["metrics"] if r["metric"] == "self_ms.self_ms")
        assert row["count"] == 4
        assert row["p50"] == 501.5
        assert row["p95"] == pytest.approx(502.85)


async def test_concurrent_leases_freeze_membership_and_extend_projection(tmp_path):
    source = MemorySource()
    started, release = asyncio.Event(), asyncio.Event()
    original = source.fetch

    async def delayed(tid, fields):
        started.set()
        await release.wait()
        return await original(tid, fields)

    source.fetch = delayed
    async with harness().open(source, work_dir=tmp_path) as session:
        dataset = await session.select()
        loader = session._loader(dataset)
        first = asyncio.create_task(loader.skeleton("t0", ("gen_ai.operation.name",)))
        await started.wait()
        second = asyncio.create_task(loader.skeleton("t0", ("gen_ai.operation.name",)))
        release.set()
        a, b = await asyncio.gather(first, second)
        assert a is not b and a["s"] is not b["s"]
        assert source.fetches == 1
        # A later structural projection must read the frozen identities, never reselect spans.
        c = await loader.skeleton("t0", ("gen_ai.operation.name", "payload"))
        assert c["s"].attrs["payload"] == "x" * 10000
        assert source.fetches == 1 and source.reads == 1


async def test_selection_failure_removes_partial_dataset(tmp_path):
    source = MemorySource()

    async def broken(query):
        yield "t0"
        raise RuntimeError("selection interrupted")

    source.select = broken
    async with harness().open(source, work_dir=tmp_path) as session:
        with pytest.raises(RuntimeError, match="interrupted"):
            await session.select()
        assert list((tmp_path / "datasets").iterdir()) == []


async def test_unused_preload_failure_and_missing_field_state(tmp_path):
    import httpx

    source = MemorySource()

    async def unavailable(refs, fields):
        raise httpx.ConnectError("offline")

    source.read = unavailable
    async with harness().open(source, work_dir=tmp_path, config=LoadConfig(lazy=False)) as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            assert ctx.trace.spans["s"].field_state("payload") == "failed"
            assert (await ctx.measure(ctx.trace.nodes[0], "self_ms")).status == "measured"
            with pytest.raises(httpx.ConnectError):
                await ctx.fact(ctx.trace.nodes[0], "size")
    source = MemorySource()
    async with harness().open(source, work_dir=tmp_path / "missing") as session:
        dataset = await session.select()
        async with session.tree(dataset, "t0") as ctx:
            del source.docs["t0"]
            with pytest.raises(EvidenceMissing):
                await ctx.fact(ctx.trace.nodes[0], "size")
            assert ctx.trace.spans["s"].field_state("payload") == "failed"


async def test_batch_invalid_rule_rejected_before_reading(tmp_path):
    source = MemorySource()
    async with harness().open(source, work_dir=tmp_path) as session:
        dataset = await session.select()
        with pytest.raises(KeyError, match="unknown detectors"):
            await session.detect(dataset, detectors=["typo"])
        assert source.fetches == source.reads == 0


async def test_opensearch_projection_pagination_and_exact_evidence():
    import httpx

    from trace_harness.ingest.sources.opensearch import OpenSearchSource
    from trace_harness.loading.model import EvidenceRef

    payloads = []
    doc = MemorySource().docs["t0"]

    def respond(request):
        body = json.loads(request.content)
        payloads.append(body)
        if "search_after" in body:
            return httpx.Response(200, json={"hits": {"hits": []}})
        hit = {
            "_id": "physical-s",
            "_index": "day-1",
            "_source": {**doc, "tags": []},
            "sort": [1, "s"],
            "inner_hits": {
                "fields": {
                    "hits": {
                        "total": {"value": 1},
                        "hits": [{"_source": {"key": "gen_ai.operation.name", "value": "chat"}}],
                    }
                }
            },
        }
        return httpx.Response(200, json={"hits": {"hits": [hit]}})

    source = OpenSearchSource(
        "https://test.invalid", "day-*", page_size=1, transport=httpx.MockTransport(respond)
    )
    try:
        spans = await source.fetch("t0", ("gen_ai.operation.name",))
        assert spans["s"].field_state("payload") == "unloaded"
        assert spans["s"].storage_id == "physical-s"
        assert payloads[1]["search_after"] == [1, "s"]
        assert "tags" not in payloads[0]["_source"]
        await source.read((EvidenceRef("t0", "s", "day-1", "physical-s"),), ("payload",))
        query = payloads[2]["query"]["bool"]["filter"][0]
        assert {"ids": {"values": ["physical-s"]}} in query["bool"]["should"][0]["bool"]["filter"]
    finally:
        await source.aclose()
    assert source.client.is_closed


@pytest.mark.parametrize(
    "result",
    [
        {"timed_out": True},
        {"_shards": {"failed": 1}},
        {
            "hits": {
                "hits": [
                    {
                        "_source": {"spanID": "s"},
                        "inner_hits": {"fields": {"hits": {"total": {"value": 2}, "hits": []}}},
                    }
                ]
            }
        },
    ],
)
async def test_opensearch_rejects_incomplete_results(result):
    import httpx

    from trace_harness.ingest.sources.opensearch import OpenSearchSource

    source = OpenSearchSource(
        "https://test.invalid",
        "day-*",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=result)),
    )
    try:
        with pytest.raises(RuntimeError):
            await source.fetch("t0", ("payload",))
    finally:
        await source.aclose()


async def test_cli_probe_survives_temporary_session(tmp_path, capsys):
    from trace_harness.cli import _cmd_single, build_parser

    doc = MemorySource().docs["t0"]
    doc["tags"].append({"key": "error", "value": True})
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps(doc) + "\n")
    args = build_parser().parse_args(["single", str(path), "--probes"])
    assert await _cmd_single(args) == 0
    evidence = list((tmp_path / "t0").glob("*.error.json"))
    assert evidence and json.loads(evidence[0].read_text())["payload"] == "x" * 10000
    assert str(evidence[0]) in capsys.readouterr().out


async def test_eager_preload_enforces_trace_budget(tmp_path):
    from trace_harness.loading.model import EvidenceTooLarge

    async with harness().open(
        MemorySource(), work_dir=tmp_path, config=LoadConfig(lazy=False, max_trace_bytes=4096)
    ) as session:
        dataset = await session.select()
        with pytest.raises(EvidenceTooLarge):
            async with session.tree(dataset, "t0"):
                pytest.fail("preloading must not suppress resource-budget failures")
