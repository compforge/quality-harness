"""Both SDKs execute the same loading policy and field-state cases."""

import asyncio
import json
from pathlib import Path

import pytest

from trace_harness import (
    EvidenceDependency,
    FactProducer,
    LoadConfig,
    TraceContributions,
    TraceHarness,
)
from trace_harness.ingest.sources.jaeger_file import normalize_es_doc
from trace_harness.kinds import genai

FIXTURE = json.loads((Path(__file__).parents[4] / "conformance/trace/loading.json").read_text())


class FixtureSource:
    namespace = "loading-conformance"

    def __init__(self):
        self.docs = {doc["traceID"]: doc for doc in FIXTURE["docs"]}
        self.reads = self.fetches = self.closes = 0

    async def select(self, query):
        for trace_id in self.docs:
            yield trace_id

    def project(self, doc, fields):
        tags = [tag for tag in doc["tags"] if fields is None or tag["key"] in fields]
        return normalize_es_doc({**doc, "tags": tags})

    async def fetch(self, trace_id, fields):
        self.fetches += 1
        span = self.project(self.docs[trace_id], fields)
        return {span.span_id: span}

    async def read(self, refs, fields):
        self.reads += 1
        return {ref.span_id: self.project(self.docs[ref.trace_id], fields) for ref in refs}

    async def aclose(self):
        self.closes += 1


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
async def test_shared_loading_contract(case, tmp_path):
    source = FixtureSource()
    producer = FactProducer(
        ("size",),
        lambda node: True,
        lambda node, trace: (EvidenceDependency((node.primary_span_id,), ("payload",)),),
        lambda node, trace: {"size": len(trace.raw_attr(node.primary_span_id)["payload"])},
    )
    harness = TraceHarness(
        TraceContributions(specs=tuple(genai.specs()), fact_producers=(producer,))
    )
    fields = None if case["fields"] is None else tuple(case["fields"])
    config = LoadConfig(lazy=case["lazy"], fields=fields)
    expected = FIXTURE["expected"]
    async with harness.open(source, work_dir=tmp_path, config=config) as session:
        dataset = await session.select()
        assert dataset.count == 1 and source.fetches == source.reads == 0
        async with session.tree(dataset, "t0") as ctx:
            node, span = ctx.trace.nodes[0], ctx.trace.spans["s"]
            assert span.field_state("payload") == case["initial"]
            assert source.reads == case["initial_reads"]
            sizes = await asyncio.gather(*(ctx.fact(node, "size") for _ in range(5)))
            assert sizes == [expected["size"]] * 5
            assert source.reads == case["final_reads"]
            assert (await ctx.measure(node, "self_ms")).values["self_ms"] == expected["self_ms"]
            await ctx.runtime.loader.load(ctx.trace, ("s",), tuple(expected["field_states"]))
            for name, state in expected["field_states"].items():
                assert span.field_state(name) == state
        with pytest.raises(RuntimeError, match="lease"):
            await ctx.fact(node, "size")
    assert source.closes == 1
