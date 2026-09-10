"""Coalesced, bounded evidence reads with durable cache reuse."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from trace_harness.ingest.sources.base import Source
from trace_harness.loading.model import EvidenceMissing, EvidenceRef, EvidenceTooLarge, LoadConfig
from trace_harness.loading.store import EvidenceStore
from trace_harness.model.context import TraceContext
from trace_harness.model.span import NormSpan


class EvidenceLoader:
    def __init__(
        self,
        source: Source,
        store: EvidenceStore,
        config: LoadConfig,
        *,
        field_aliases=None,
        normalize_span=None,
    ):
        self.source, self.store, self.config = source, store, config
        self.field_aliases = field_aliases or {}
        self.normalize_span = normalize_span
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._locks = [asyncio.Lock() for _ in range(config.concurrency)]
        self._skeleton_locks = [asyncio.Lock() for _ in range(config.concurrency)]
        self._pending: dict[str, list] = {}
        self._tasks: set[asyncio.Task] = set()
        self.stats = {"cache_hits": 0, "reads": 0, "bytes_read": 0}
        self._closed = False

    async def skeleton(self, trace_id: str, fields: tuple[str, ...]):
        # A trace may have multiple concurrent leases. Freeze membership exactly once.
        async with self._skeleton_locks[hash(trace_id) % len(self._skeleton_locks)]:
            return await self._skeleton_locked(trace_id, fields)

    async def _skeleton_locked(self, trace_id, fields):
        key = f"skeleton:{trace_id}"
        saved = self.store.get(key)
        if saved is not None:
            spans = {sid: NormSpan(**value) for sid, value in saved["spans"].items()}
            extra = tuple(set(fields) - set(saved["fields"]))
            if extra:
                from trace_harness.model.context import TraceContext

                trace = TraceContext(trace_id, spans, [], {})
                await self.load(trace, tuple(spans), extra)
                self.store.put(
                    key,
                    {
                        "fields": sorted(set(fields) | set(saved["fields"])),
                        "spans": {sid: asdict(span) for sid, span in spans.items()},
                    },
                )
            return spans
        async with self._semaphore:
            spans = await self.source.fetch(trace_id, fields)
        if not spans:
            raise EvidenceMissing(f"trace has no available spans: {trace_id}")
        self._check_size(spans)
        for span in spans.values():
            span.loaded_fields = fields
        self.store.put(
            key, {"fields": fields, "spans": {sid: asdict(span) for sid, span in spans.items()}}
        )
        for span in spans.values():
            self.store.save(trace_id, span, fields)
        return spans

    def _check_size(self, spans):
        size = sum(len(json.dumps(s.raw).encode()) for s in spans.values())
        if size > min(
            self.config.max_trace_bytes, self.config.cache_bytes // self.config.active_traces
        ):
            raise EvidenceTooLarge(f"trace requires {size} bytes, exceeding loading budget")
        return size

    async def load(
        self, trace: TraceContext, span_ids: tuple[str, ...], fields: tuple[str, ...] | None
    ):
        if self._closed:
            raise RuntimeError("evidence loader is closed")
        if all(
            sid in trace.spans
            and (
                trace.spans[sid].loaded_fields is None
                or (fields is not None and set(fields).issubset(trace.spans[sid].loaded_fields))
            )
            for sid in span_ids
        ):
            return
        future = asyncio.get_running_loop().create_future()
        queue = self._pending.setdefault(trace.trace_id, [])
        queue.append((trace, span_ids, fields, future))
        if len(queue) == 1:
            task = asyncio.create_task(self._flush(trace.trace_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        await future

    async def _flush(self, trace_id):
        async with self._locks[hash(trace_id) % len(self._locks)]:
            await self._flush_locked(trace_id)

    async def _flush_locked(self, trace_id):
        # One event-loop turn collects concurrent requests without a latency timer.
        await asyncio.sleep(0)
        requests = self._pending.pop(trace_id, [])
        if not requests:
            return
        fields = (
            None
            if any(r[2] is None for r in requests)
            else tuple(sorted({f for r in requests for f in r[2]}))
        )
        spans = {
            sid: trace.spans[sid]
            for trace, ids, _, _ in requests
            for sid in ids
            if sid in trace.spans
        }
        try:
            unknown = {sid for _, ids, _, _ in requests for sid in ids} - spans.keys()
            if unknown:
                raise EvidenceMissing(f"span absent from frozen skeleton: {sorted(unknown)}")
            found, missing = {}, []
            for sid, span in spans.items():
                cached = self.store.read(trace_id, sid, fields)
                if cached is not None:
                    self.stats["cache_hits"] += 1
                    found[sid] = self.normalize_span(cached) if self.normalize_span else cached
                else:
                    missing.append(EvidenceRef(trace_id, sid, span.storage_index, span.storage_id))
            # Bounded batches avoid oversized bool queries and backend clause limits.
            for offset in range(0, len(missing), 100):
                refs = tuple(missing[offset : offset + 100])
                async with self._semaphore:
                    requested_fields = (
                        None
                        if fields is None
                        else tuple(
                            set(fields)
                            | {
                                alias
                                for alias, canonical in self.field_aliases.items()
                                if canonical in fields
                            }
                        )
                    )
                    fetched = await self.source.read(refs, requested_fields)
                self.stats["reads"] += 1
                self.stats["bytes_read"] += self._check_size(fetched)
                for sid, span in fetched.items():
                    if self.normalize_span:
                        span = self.normalize_span(span)
                    self.store.save(trace_id, span, fields)
                    found[sid] = span
            for trace, ids, requested, future in requests:
                if future.done():
                    continue
                absent = set(ids) - found.keys()
                if absent:
                    for sid in absent:
                        for name in requested or ("*",):
                            trace.spans[sid].field_errors[name] = "EvidenceMissing"
                    future.set_exception(
                        EvidenceMissing(f"evidence expired or missing: {trace_id}/{sorted(absent)}")
                    )
                    continue
                for sid in ids:
                    incoming, target = found[sid], trace.spans[sid]
                    attrs = (
                        incoming.attrs
                        if requested is None
                        else {k: v for k, v in incoming.attrs.items() if k in requested}
                    )
                    target.attrs.update(attrs)
                    tags = {t["key"]: t for t in target.raw.get("tags", [])}
                    tags.update(
                        {
                            t["key"]: t
                            for t in incoming.raw.get("tags", [])
                            if requested is None or t["key"] in requested
                        }
                    )
                    target.raw = {**incoming.raw, "tags": list(tags.values())}
                    target.loaded_fields = (
                        None
                        if requested is None or target.loaded_fields is None
                        else tuple(set(target.loaded_fields) | set(requested))
                    )
                    for name in requested or ("*",):
                        target.field_errors.pop(name, None)
                    if requested is None:
                        target.error_events, target.events = incoming.error_events, incoming.events
                self._check_size(trace.spans)
                future.set_result(None)
        except BaseException as exc:
            for trace, ids, fields, future in requests:
                for sid in ids:
                    if sid in trace.spans:
                        for name in fields or ("*",):
                            trace.spans[sid].field_errors[name] = type(exc).__name__
                if not future.done():
                    if isinstance(exc, asyncio.CancelledError):
                        future.cancel()
                    else:
                        future.set_exception(exc)

    async def close(self):
        self._closed = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
