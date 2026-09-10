"""Per-trace dependency evaluation, owned by one analysis run."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trace_harness.harness import TraceHarness
    from trace_harness.loading.loader import EvidenceLoader
    from trace_harness.model.context import TraceContext

from trace_harness.analyze.measure import measure
from trace_harness.loading.facts import EvidenceDependency, FactDependency
from trace_harness.loading.http import HTTP_EVIDENCE
from trace_harness.model.measurement import Measurement, Measurements
from trace_harness.model.node import Node
from trace_harness.model.span import error_text

_ACTIVE = ContextVar("trace_fact_stack", default=())


class TraceAnalysis:
    def __init__(self, harness: TraceHarness, trace: TraceContext, loader: EvidenceLoader):
        self.harness, self.trace, self.loader = harness, trace, loader
        self.measurements = Measurements()
        self._facts = {}
        self._waiting = {}
        self._metrics = {}
        self.active = True

    async def dependencies(self, dependencies):
        async def prepare(dependency):
            if isinstance(dependency, EvidenceDependency):
                await self.loader.load(self.trace, dependency.span_ids, dependency.fields)
            elif isinstance(dependency, FactDependency):
                await self.fact(self.trace.view().by_id[dependency.node_id], dependency.name)

        await asyncio.gather(*(prepare(dep) for dep in dependencies))

    async def fact(self, node: Node, name: str):
        if not self.active:
            raise RuntimeError("trace lease has ended; reacquire it from the dataset")
        if self.trace.view().by_id.get(node.node_id) is not node:
            raise ValueError("node does not belong to this trace")
        key = (node.node_id, name)
        if key in _ACTIVE.get():
            raise ValueError(f"cyclic fact dependency: {key}")
        parent = _ACTIVE.get()[-1] if _ACTIVE.get() else None
        if parent:
            self._waiting.setdefault(parent, set()).add(key)

            def reaches(current, seen):
                if current == parent:
                    return True
                if current in seen:
                    return False
                seen.add(current)
                return any(reaches(child, seen) for child in self._waiting.get(current, ()))

            if reaches(key, set()):
                self._waiting[parent].discard(key)
                raise ValueError(f"cyclic fact dependency: {key}")
        if key not in self._facts:
            self._facts[key] = asyncio.create_task(self._fact(node, name))
        try:
            return await asyncio.shield(self._facts[key])
        finally:
            if parent:
                self._waiting[parent].discard(key)

    async def _fact(self, node, name):
        token = _ACTIVE.set((*_ACTIVE.get(), (node.node_id, name)))
        try:
            spec = self.trace.specs[node.kind]
            if name in spec.detail_facts:
                # A kind may retain its pure builder for a group of related detail facts.
                # Rebuilding facts never reclassifies or reconnects nodes.
                await self.loader.load(self.trace, tuple(node.span_ids), spec.detail_fields)
                values = spec.build(
                    self.trace.spans[node.primary_span_id],
                    [self.trace.spans[s] for s in node.span_ids if s != node.primary_span_id],
                )
                node.facts.update({k: v for k, v in values.items() if k in spec.detail_facts})
                return node.facts.get(name)
            if name in node.facts:
                return node.facts[name]
            producers = [
                p
                for p in (HTTP_EVIDENCE, *self.harness.contributions.fact_producers)
                if name in p.produces and p.applies(node)
            ]
            transforms = [
                t for t in self.harness.transforms if name in t.produces and t.applies(node)
            ]
            if len(producers) + len(transforms) > 1:
                raise ValueError(f"conflicting fact producers: {node.kind}/{name}")
            if producers:
                producer = producers[0]
                await self.dependencies(producer.requires(node, self.trace))
                values = producer.compute(node, self.trace)
                if set(values) - set(producer.produces):
                    raise ValueError(f"undeclared fact outputs: {name}")
                node.facts.update(values)
            elif transforms:
                transform = transforms[0]
                await self.dependencies(transform.requires(node, self.trace))
                self.trace.transforms.materialize(((node, name),))
            return node.facts.get(name)
        finally:
            _ACTIVE.reset(token)

    async def metric(self, node: Node, name: str) -> Measurement | None:
        if not self.active:
            raise RuntimeError("trace lease has ended")
        if name not in self._metrics:
            self._metrics[name] = asyncio.create_task(self._metric(name))
        await asyncio.shield(self._metrics[name])
        return self.measurements.get(node.node_id, name)

    async def _metric(self, name):
        selected = [m for m in self.harness.measurers if m.spec.id == name]
        if not selected:
            raise KeyError(f"unknown measurement: {name}")
        try:
            for measurer in selected:
                await self.dependencies(measurer.requires(self.trace))
            result = measure(self.trace, selected)
        except Exception as exc:
            result = Measurements(
                [m.spec for m in selected],
                [],
                {
                    node.node_id: [Measurement(name, node.node_id, "error", error=str(exc))]
                    for node in self.trace.nodes
                },
            )
        self.measurements.specs.extend(result.specs)
        if result.sources:
            self.measurements.sources = result.sources
        for node_id, values in result.results.items():
            self.measurements.results.setdefault(node_id, []).extend(values)

    async def prepare_view(self, *, full=False):
        if full:
            await self.loader.load(
                self.trace,
                tuple(sid for sid, s in self.trace.spans.items() if s.raw.get("spanID")),
                None,
            )
        for node in self.trace.nodes:
            spec = self.trace.specs[node.kind]
            names = tuple(
                dict.fromkeys((*spec.project_requires, *(spec.detail_facts if full else ())))
            )
            await asyncio.gather(*(self.fact(node, name) for name in names))
            if spec.project:
                node.brief = spec.project(node)
            if node.has_error:
                node.error_text = error_text(self.trace.spans[node.error_anchor])

    async def close(self):
        self.active = False
        tasks = (*self._facts.values(), *self._metrics.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
