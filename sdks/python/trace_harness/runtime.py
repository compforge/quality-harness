"""One managed execution path for fixed datasets, including single-member datasets."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from trace_harness.ingest.sources.base import Source

if TYPE_CHECKING:
    from trace_harness.batch import BatchResult
    from trace_harness.harness import TraceHarness

from trace_harness.analyze.context import AnalysisContext
from trace_harness.analyze.diagnose import diagnose
from trace_harness.analyze.diagnose.registry import DetectorRegistry
from trace_harness.dataset import Dataset
from trace_harness.ingest.assemble import assemble
from trace_harness.ingest.sources.base import SpanQuery
from trace_harness.loading.analysis import TraceAnalysis
from trace_harness.loading.loader import EvidenceLoader
from trace_harness.loading.model import EvidenceTooLarge, LoadConfig
from trace_harness.loading.store import EvidenceStore

# Generic structural and inexpensive quantitative metadata. Business additions are explicit.
STRUCTURE_FIELDS = (
    "asgi.event.type",
    "http.target",
    "server.address",
    "server.port",
    "net.peer.name",
    "net.peer.port",
    "span.kind",
    "http.url",
    "url.full",
    "url.path",
    "http.method",
    "http.request.method",
    "http.status_code",
    "http.response.status_code",
    "http.route",
    "otel.status_code",
    "error",
    "otel.status_description",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "llm.model_name",
    "llm.response.model",
    "gen_ai.tool.name",
    "gen_ai.agent.name",
    "gen_ai.agent.id",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.total_tokens",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
    "llm.token_count.prompt",
    "llm.token_count.completion",
    "llm.token_count.total",
    "http.request_id",
    "request.id",
)


class TraceSession:
    def __init__(
        self,
        harness: TraceHarness,
        source: Source,
        *,
        work_dir: str | Path | None = None,
        config: LoadConfig | None = None,
    ):
        self.harness, self.source = harness, source
        self.config = config or LoadConfig()
        self._temp = (
            tempfile.TemporaryDirectory(prefix="trace-harness-") if work_dir is None else None
        )
        self.work_dir = Path(self._temp.name if self._temp else work_dir)
        self._stores = {}
        self._loaders = {}
        self._slots = asyncio.Semaphore(self.config.active_traces)
        self._closed = False
        self._leases = set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._closed = True
        for lease in tuple(self._leases):
            await lease.close()
        for loader in self._loaders.values():
            await loader.close()
        for store in self._stores.values():
            store.close()
        await self.source.aclose()
        if self._temp:
            self._temp.cleanup()

    async def select(self, query: SpanQuery | None = None) -> Dataset:
        if self._closed:
            raise RuntimeError("session is closed")
        query = query or SpanQuery()
        dataset_id = uuid4().hex
        path = self.work_dir / "datasets" / dataset_id
        path.mkdir(parents=True)
        count = 0
        members_db = sqlite3.connect(path / "index.sqlite")
        try:
            members_db.execute("CREATE TABLE members(trace TEXT PRIMARY KEY)")
            with (path / "members.jsonl.tmp").open("w") as stream:
                async for trace_id in self.source.select(query):
                    if not members_db.execute(
                        "INSERT OR IGNORE INTO members VALUES(?)", (trace_id,)
                    ).rowcount:
                        continue
                    stream.write(json.dumps(trace_id) + "\n")
                    count += 1
            members_db.commit()
        except BaseException:
            members_db.close()
            shutil.rmtree(path)
            raise
        finally:
            members_db.close()
        (path / "members.jsonl.tmp").replace(path / "members.jsonl")
        (path / "manifest.json").write_text(
            json.dumps(
                {
                    "id": dataset_id,
                    "source": self.source.namespace,
                    "query": asdict(query),
                    "count": count,
                    "limit_reached": count >= query.limit,
                },
                indent=2,
            )
        )
        return Dataset(dataset_id, self.source.namespace, path)

    def _loader(self, dataset: Dataset) -> EvidenceLoader:
        if self._closed:
            raise RuntimeError("session is closed")
        if dataset.source != self.source.namespace:
            raise ValueError("dataset belongs to a different source")
        if dataset.id not in self._loaders:
            store = EvidenceStore(dataset.path)
            self._stores[dataset.id] = store
            self._loaders[dataset.id] = EvidenceLoader(
                self.source,
                store,
                self.config,
                field_aliases=self.harness.contributions.field_aliases,
                normalize_span=self.harness.contributions.normalize_span,
            )
        return self._loaders[dataset.id]

    @asynccontextmanager
    async def tree(self, dataset: Dataset, trace_id: str) -> AsyncIterator[AnalysisContext]:
        if not dataset.contains(trace_id):
            raise KeyError(f"trace is not a member of dataset: {trace_id}")
        async with self._slots:
            loader = self._loader(dataset)
            fields = tuple(
                sorted(
                    set(STRUCTURE_FIELDS).union(
                        self.harness.contributions.structure_fields,
                        *(spec.structure_fields for spec in self.harness.specs),
                    )
                )
            )
            spans = await loader.skeleton(trace_id, fields)
            if self.harness.contributions.prepare_spans:
                spans = self.harness.contributions.prepare_spans(spans)
            trace = assemble(
                spans, self.harness.specs, transforms=self.harness.transforms, prepare=False
            )
            trace.trace_id = trace_id
            trace.evidence_dir = dataset.path / "evidence" / trace_id
            runtime = TraceAnalysis(self.harness, trace, loader)
            self._leases.add(runtime)
            try:
                if not self.config.lazy:
                    # Preload errors are recorded by the loader and surface only if consumed.
                    try:
                        await loader.load(
                            trace,
                            tuple(sid for sid, s in trace.spans.items() if s.raw.get("spanID")),
                            self.config.fields,
                        )
                    except EvidenceTooLarge:
                        # Resource budgets apply even when preload evidence is not consumed.
                        raise
                    except Exception:
                        pass  # The loader retains per-field errors for subsequent consumers.
                yield AnalysisContext(trace, runtime.measurements, {}, runtime)
            finally:
                await runtime.close()
                self._leases.discard(runtime)

    async def trees(self, dataset: Dataset) -> AsyncIterator[AnalysisContext]:
        for trace_id in dataset.members():
            async with self.tree(dataset, trace_id) as analysis:
                yield analysis

    async def analyze(
        self,
        analysis: AnalysisContext,
        *,
        detectors: list[str] | None = None,
        metrics: list[str] | None = None,
        probes: bool = False,
    ) -> AnalysisContext:
        metric_ids = [m.spec.id for m in self.harness.measurers] if metrics is None else metrics
        if analysis.trace.nodes:
            await asyncio.gather(
                *(analysis.measure(analysis.trace.nodes[0], name) for name in metric_ids)
            )
        if detectors is not None:
            registered = self.harness.detectors.registered()
            chosen = [d for d in registered if d.__name__ in detectors]
            unknown = set(detectors) - {d.__name__ for d in chosen}
            if unknown:
                raise KeyError(f"unknown detectors: {sorted(unknown)}")
            # An explicit detector selection does not run unrelated baseline rules.
            found = {}
            from trace_harness.analyze.diagnose import _post_order

            for node in _post_order(analysis.trace):
                for detector in chosen:
                    result = detector(node, analysis)
                    for finding in (
                        await result if isinstance(result, Awaitable) else result
                    ) or []:
                        found.setdefault(finding.node_id, []).append(finding)
        else:
            # Kind metrics may themselves depend on evidence (e.g. tool result size).
            await asyncio.gather(
                *(
                    analysis.fact(n, name)
                    for n in analysis.trace.nodes
                    for name in analysis.trace.specs[n.kind].metrics
                )
            )
            found = await diagnose(
                analysis.trace,
                probes=probes,
                detector_registry=DetectorRegistry(self.harness.detectors.registered()),
                analysis=analysis,
            )
        return AnalysisContext(analysis.trace, analysis.measurements, found, analysis.runtime)

    async def prepare_view(
        self, analysis: AnalysisContext, *, full: bool = False
    ) -> AnalysisContext:
        if analysis.runtime is None:
            raise ValueError("view preparation requires an active trace lease")
        await analysis.runtime.prepare_view(full=full)
        return analysis

    async def detect(
        self,
        dataset: Dataset,
        *,
        detectors: list[str] | None = None,
        metrics: list[str] | None = None,
        batch_detectors: list[str] | None = None,
    ) -> BatchResult:
        from trace_harness.batch import run_batch

        return await run_batch(
            self, dataset, detectors=detectors, metrics=metrics, batch_detectors=batch_detectors
        )
