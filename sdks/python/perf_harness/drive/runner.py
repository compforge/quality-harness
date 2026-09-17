"""The Runner — the per-service protocol adapter (one of the two extension points).

A ``Runner`` is *how to fire*: given a ``Case`` (the request input, as data), it
fires one request against the Service and returns an ``Outcome``. It deliberately
does **not** hold payloads or decide *which* Case to fire — Cases are data
(authored in the consumer repo, like an eval_harness evalset) and the Engine
selects one per fire by weight. So the Runner only knows the protocol (e.g.
POST + SSE drain). The framework ships only the base class, the ``stream_sse``
helper, and ``MockRunner`` (offline self-test); a real service registers its
adapter via ``register_runner`` from its own project.

It owns a thin httpx firing path rather than importing any sibling harness —
perf_harness is self-contained.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from harness_common import Operation
from spec_case.model import Case

from perf_harness.metric import FacetDescriptor, MetricFamily
from perf_harness.model import Outcome, ResourceProfile, Service

if TYPE_CHECKING:
    from perf_harness.drive.load import LoadPlan


@dataclass(frozen=True)
class ArmContext:
    """Immutable handles and coordinates shared throughout one ArmRun."""

    service: Service
    client: httpx.AsyncClient
    run_id: str
    resources: ResourceProfile
    load: LoadPlan


@dataclass(frozen=True)
class FireContext:
    """One request dispatch within a ArmRun.

    ``arm_run`` is shared by concurrent fires; the selected ``case`` belongs to this
    dispatch only. Composition keeps the shared ArmContext immutable instead of
    mutating it with request-scoped state while the load generator runs concurrently.
    """

    arm_run: ArmContext
    case: Case


class Runner(ABC):
    """Per-service protocol adapter: fire one Case, return its Outcome."""

    #: stable id used by the runner registry (build_runner / config ``runner.name``)
    name: str = "runner"

    def operation(self, ctx: FireContext) -> Operation:
        """Identify the Service capability exercised by this fire.

        A protocol adapter that serves multiple Operations can override this
        from the Case input. The stable default treats the registered Runner
        name as the capability name.
        """
        return Operation(name=self.name)

    async def setup(self, ctx: ArmContext) -> None:
        """Prepare external state before measurement and observation begin.

        A partially completed setup is still followed by ``cleanup`` so the
        runner can release anything it already created.
        """
        return None

    async def deactivate(self, ctx: ArmContext) -> None:
        """Deactivate arm_run-scoped runner state after measurement.

        Probes keep sampling while this hook issues normal stop/release requests.
        The configured cooldown starts after it returns.
        """
        return None

    async def cleanup(self, ctx: ArmContext) -> None:
        """Finally clean up arm_run-scoped external state after observation stops."""
        return None

    @abstractmethod
    async def fire(self, ctx: FireContext) -> Outcome:
        """Fire one request built from ``ctx.case.input`` against its ArmRun Service.

        ``fire`` records the *raw observation* only (status, timing, frame/byte
        counts, protocol signals in ``outcome.meta``) — it does NOT decide
        success: ``judge`` does, and the Engine runs it. ``ctx.arm_run.run_id`` is
        this run's id — a biz adapter may weave it into the request to tie
        traffic to the run (e.g. a chat conversation id ``f"{run_id}-{uuid}"``).
        The Engine stamps ``ctx.case.facets`` onto the returned Outcome, so a Runner
        need not set them — but it MAY add runtime-derived facets to
        ``outcome.facets`` (e.g. ``{"heavy": "yes"}``).
        """

    def describe(self) -> list[MetricFamily]:
        """Declare the request-side metric FAMILIES this Runner records on the Outcome.

        Symmetric with ``Probe.describe`` (the resource side): it lets the
        Runner state a metric's ``unit``/``source`` explicitly instead of letting
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
    timeout: float = 120,
    done_marker: bytes | None = None,
    on_frame: Callable[[float, bytes], None] | None = None,
    max_event_bytes: int = 1048576,
) -> Outcome:
    """Adapt shared streaming observations to the perf Outcome contract."""
    from dataclasses import asdict

    from harness_toolbox.sse import stream_sse as observe_sse

    return Outcome(
        **asdict(
            await observe_sse(
                client,
                url,
                json=json,
                headers=headers,
                timeout=timeout,
                done_marker=done_marker,
                on_frame=on_frame,
                max_event_bytes=max_event_bytes,
            )
        )
    )


class MockRunner(Runner):
    """A zero-dependency Runner for smoke runs and tests (no live Service).

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
# Registry — lets a config `runner: <name>` resolve to a Runner instance.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Callable[[dict], Runner]] = {}


def register_runner(name: str, factory: Callable[[dict], Runner]) -> None:
    """Register a per-service Runner factory under ``name``.

    The framework bundles only ``mock``; a real service registers its Runner
    from its own project so ``runner: <name>`` in a config resolves, e.g.::

        register_runner("chat", lambda cfg: ChatRunner(**cfg))
    """
    _REGISTRY[name] = factory


def build_runner(name: str, cfg: dict) -> Runner:
    """Resolve a runner name + its config block into a Runner instance."""
    if name == "mock":
        return MockRunner(base_ms=float(cfg.get("base_ms", 20.0)))
    if name in _REGISTRY:
        return _REGISTRY[name](cfg)
    raise ValueError(
        f"unknown runner {name!r}; register it in your project via "
        f"perf_harness.register_runner({name!r}, ...)"
    )
