"""The Workload — the per-service protocol adapter (one of the two extension points).

A ``Workload`` is *how to fire*: given a ``Case`` (the request input, as data), it
fires one request against the Service and returns an ``Outcome``. It deliberately
does **not** hold payloads or decide *which* Case to fire — Cases are data
(authored in the consumer repo, like an eval_harness evalset) and the Engine
selects one per fire by weight. So the Workload only knows the protocol (e.g.
POST + SSE drain). The framework ships only the base class, the ``stream_sse``
helper, and ``MockWorkload`` (offline self-test); a real service registers its
adapter via ``register_workload`` from its own project.

It owns a thin httpx firing path rather than importing any sibling harness —
perf_harness is self-contained.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from harness_common import Operation
from spec_case.model import Case

from perf_harness.metric import FacetDescriptor, MetricFamily
from perf_harness.model import Outcome, ResourceProfile, Service, Verdict

if TYPE_CHECKING:
    from perf_harness.drive.load import LoadProfile


@dataclass(frozen=True)
class TrialContext:
    """Immutable handles and coordinates shared throughout one Trial."""

    service: Service
    client: httpx.AsyncClient
    run_id: str
    resources: ResourceProfile
    load: LoadProfile


@dataclass(frozen=True)
class FireContext:
    """One request dispatch within a Trial.

    ``trial`` is shared by concurrent fires; the selected ``case`` belongs to this
    dispatch only. Composition keeps the shared TrialContext immutable instead of
    mutating it with request-scoped state while the load generator runs concurrently.
    """

    trial: TrialContext
    case: Case


class Workload(ABC):
    """Per-service protocol adapter: fire one Case, return its Outcome."""

    #: stable id used by the workload registry (build_workload / config ``workload.name``)
    name: str = "workload"

    def operation(self, ctx: FireContext) -> Operation:
        """Identify the Service capability exercised by this fire.

        A protocol adapter that serves multiple Operations can override this
        from the Case input. The stable default treats the registered Workload
        name as the capability name.
        """
        return Operation(name=self.name)

    async def setup(self, ctx: TrialContext) -> None:
        """Prepare external state before measurement and observation begin.

        A partially completed setup is still followed by ``cleanup`` so the
        workload can release anything it already created.
        """
        return None

    async def deactivate(self, ctx: TrialContext) -> None:
        """Deactivate trial-scoped workload state after measurement.

        Probes keep sampling while this hook issues normal stop/release requests.
        The configured cooldown starts after it returns.
        """
        return None

    async def cleanup(self, ctx: TrialContext) -> None:
        """Finally clean up trial-scoped external state after observation stops."""
        return None

    @abstractmethod
    async def fire(self, ctx: FireContext) -> Outcome:
        """Fire one request built from ``ctx.case.input`` against its Trial Service.

        ``fire`` records the *raw observation* only (status, timing, frame/byte
        counts, protocol signals in ``outcome.meta``) — it does NOT decide
        success: ``judge`` does, and the Engine runs it. ``ctx.trial.run_id`` is
        this run's id — a biz adapter may weave it into the request to tie
        traffic to the run (e.g. a chat conversation id ``f"{run_id}-{uuid}"``).
        The Engine stamps ``ctx.case.facets`` onto the returned Outcome, so a Workload
        need not set them — but it MAY add runtime-derived facets to
        ``outcome.facets`` (e.g. ``{"heavy": "yes"}``).
        """

    def judge(self, outcome: Outcome) -> Verdict:
        """Verdict on one (raw) Outcome — the request-side success/error rule.

        Default: a transport error (``meta["exc"]``) → its exception name; a
        non-2xx status → the status; otherwise OK. Override per-service for
        protocol-specific failures that HTTP status can't express — e.g. an SSE
        endpoint that returns 200 then emits an error frame, truncates before its
        terminal sentinel, or streams nothing — by reading ``outcome.events`` /
        ``outcome.meta``. Must be **pure and cheap**: it runs once per Outcome and
        may be re-run offline over stored Outcomes (re-judge without re-firing).
        """
        if outcome.meta.get("exc"):
            return Verdict(False, outcome.meta["exc"])
        if outcome.status is not None and 200 <= outcome.status < 300:
            return Verdict(True)
        return Verdict(False, str(outcome.status) if outcome.status is not None else "unknown")

    def describe(self) -> list[MetricFamily]:
        """Declare the request-side metric FAMILIES this Workload records on the Outcome.

        Symmetric with ``Probe.describe`` (the resource side): it lets the
        Workload state a metric's ``unit``/``source`` explicitly instead of letting
        the Engine infer them (unit from the key suffix, source ``client``).

        OPTIONAL and partial — any ``Outcome.metrics`` key you DON'T declare still
        gets the inferred descriptor, so open-ended, runtime-discovered milestones
        (e.g. ``first_<event>_ms`` stamped per SSE event) need no declaration.
        Declare only the ones the suffix convention can't capture: a non-``_ms``
        unit (``prompt_tokens`` → ``tok``) or a server-sourced number echoed in a
        frame (``source="server"``). Returned descriptors must be
        ``side="request"``, ``value_kind="distribution"``. Declaring a metric also
        lets an SLO reference ``<name>.<stat>`` be validated at parse.
        """
        return []

    def describe_facets(self) -> list[FacetDescriptor]:
        """Declare the runtime facets ``fire()`` may stamp on the Outcome (beyond
        the static Case mix), e.g. ``heavy=yes|no``. Only declared facets can be
        referenced by an SLO ``{facet="val"}`` label — an undeclared runtime facet
        still appears in the report but can't gate a run (it has no static schema
        to validate against, which would let a typo'd gate silently pass)."""
        return []


async def stream_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
    done_marker: bytes | None = None,
    on_frame: Callable[[float, bytes], None] | None = None,
) -> Outcome:
    """Fire one POST SSE stream, drain it, and return a *raw* Outcome (no verdict).

    Records the ``ttft_ms`` per_request metric (time to first byte) into
    ``Outcome.metrics`` for any stream. If ``on_frame`` is given it is called
    ``on_frame(t_ms, data)`` once per SSE ``data:`` frame — ``t_ms`` is ms since
    request start, ``data`` the bytes after the ``data:`` prefix (stripped) — so a
    Workload can record per-event milestones (first ``message`` → a
    ``first_message_ms`` metric) and read protocol signals into ``meta`` for
    ``judge``; frames are then counted by complete ``data:`` lines. Without
    ``on_frame``, frames are counted cheaply by ``data:`` boundaries.

    Any transport/timeout error is recorded into ``meta["exc"]`` (the exception
    class name) and ``meta["exc_detail"]`` (its message) rather than raised, so
    the load generator keeps running. If
    ``done_marker`` is given, ``meta["saw_done"]`` records whether that terminal
    sentinel (e.g. ``b"[DONE]"``) was seen. The success/error verdict is NOT
    decided here; that's ``Workload.judge``'s job.
    """
    t0 = time.monotonic()
    status: int | None = None
    events = 0
    nbytes = 0
    ttft_ms: float | None = None
    saw_done = False
    meta: dict = {}
    metrics: dict[str, float] = {}
    buf = b""
    try:
        async with client.stream("POST", url, json=json, headers=headers, timeout=timeout) as r:
            status = r.status_code
            async for chunk in r.aiter_bytes():
                if not chunk:
                    continue
                if ttft_ms is None:
                    ttft_ms = (time.monotonic() - t0) * 1000.0
                nbytes += len(chunk)
                if done_marker is not None and done_marker in chunk:
                    saw_done = True
                if on_frame is None:
                    events += chunk.count(b"data:")
                    continue
                buf += chunk
                # SSE frames are newline-delimited; keep the trailing partial in buf
                *complete, buf = buf.split(b"\n")
                for line in complete:
                    data = _sse_data(line)
                    if data is not None:
                        events += 1
                        on_frame((time.monotonic() - t0) * 1000.0, data)
            if on_frame is not None:
                data = _sse_data(buf)  # last frame may arrive without a trailing newline
                if data is not None:
                    events += 1
                    on_frame((time.monotonic() - t0) * 1000.0, data)
    except Exception as e:  # noqa: BLE001 — load gen must survive any client error
        meta["exc"] = type(e).__name__
        meta["exc_detail"] = str(e)
    if done_marker is not None:
        meta["saw_done"] = saw_done
    if ttft_ms is not None:
        metrics["ttft_ms"] = ttft_ms
    return Outcome(
        status=status,
        duration_ms=(time.monotonic() - t0) * 1000.0,
        events=events,
        nbytes=nbytes,
        meta=meta,
        metrics=metrics,
    )


def _sse_data(line: bytes) -> bytes | None:
    """The bytes after an SSE ``data:`` prefix (stripped), or None if not a data line."""
    if not line.startswith(b"data:"):
        return None
    return line[len(b"data:") :].strip()


class MockWorkload(Workload):
    """A zero-dependency Workload for smoke runs and tests (no live Service).

    Sleeps ``case.input["ms"]`` (or ``base_ms``) + a deterministic jitter and
    reports success — so the Engine/Probe/Report pipeline (and per-facet
    aggregation, by giving cases different ``ms`` + ``facets``) can be exercised
    offline.
    """

    name = "mock"

    def __init__(self, base_ms: float = 20.0) -> None:
        self.base_ms = base_ms
        self._i = 0

    async def fire(self, ctx: FireContext) -> Outcome:
        import asyncio

        self._i += 1
        jitter = (self._i % 7) * 2.0
        dur = float(ctx.case.input.get("ms", self.base_ms)) + jitter
        await asyncio.sleep(dur / 1000.0)
        # raw observation only — base judge() turns status 200 into ok=True
        return Outcome(status=200, duration_ms=dur, events=1)


# ---------------------------------------------------------------------------
# Registry — lets a config `workload: <name>` resolve to a Workload instance.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Callable[[dict], Workload]] = {}


def register_workload(name: str, factory: Callable[[dict], Workload]) -> None:
    """Register a per-service Workload factory under ``name``.

    The framework bundles only ``mock``; a real service registers its Workload
    from its own project so ``workload: <name>`` in a config resolves, e.g.::

        register_workload("chat", lambda cfg: ChatWorkload(**cfg))
    """
    _REGISTRY[name] = factory


def build_workload(name: str, cfg: dict) -> Workload:
    """Resolve a workload name + its config block into a Workload instance."""
    if name == "mock":
        return MockWorkload(base_ms=float(cfg.get("base_ms", 20.0)))
    if name in _REGISTRY:
        return _REGISTRY[name](cfg)
    raise ValueError(
        f"unknown workload {name!r}; register it in your project via "
        f"perf_harness.register_workload({name!r}, ...)"
    )
