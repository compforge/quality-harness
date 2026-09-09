"""The unified metric model — perf has exactly ONE metric concept.

Three layers, Prometheus-typed in spirit but not in storage (see docs/metric-model.md):

  - ``MetricFamily``: name + unit + **side** + **value_kind** + source — the
    metric's identity declared ONCE, with NO concrete labels. ``value_kind``
    (counter/gauge/distribution/scalar) decides which ``stat`` is legal, exactly
    like Prometheus's type decides valid functions. ``side`` says which slice axes
    the family's series live on — ``request`` series slice by facet,
    ``resource`` series by service (and only those; the SLO parser enforces it).
    A concrete *series* is the family plus labels (``top.cpu_m{service="chat"}``)
    — labels are NOT baked into the family, so its unit/value_kind/description are
    never duplicated per series.
  - a per-``value_kind`` summary union (``CounterSummary`` / ``GaugeSummary`` /
    ``DistributionSummary`` / ``ScalarSummary``) — typed, not one nullable matrix.
    Each summary carries its own ``caveats`` (CO-bias, saturation, thin samples),
    so a number's trustworthiness travels WITH it and a gate/report can never read
    a biased value as if it were clean.
  - ``Missing``: ``query`` returns a value OR a ``Missing`` (with a reason), never a
    bare ``None`` — a consumer must handle absence explicitly (an SLO maps it to
    *skipped*, never silently to pass).

Everything (report / SLO / CSV / HTML) reads through one resolver: ``<name>{labels}.<stat>``.
This module is pure (stdlib only) and the resolver works on plain dicts
(``{series_id: MetricSummary}`` + ``{family_name: MetricFamily}``), so it never
imports model.py — keeping the dependency DAG ``metric ← model ← {store, engine}`` acyclic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Which storage/slice side a family's series live on. ``request`` = computed from
# Outcomes, sliceable by facet within any Window. ``resource`` = Probe samples,
# sliceable by service within the same Window. This is the ONE routing bit the
# store/report/SLO need; how a value
# was produced is plain metadata (``source``), not a type axis.
MetricSide = Literal["request", "resource"]
MetricValueKind = Literal["counter", "gauge", "distribution", "scalar"]

# A summary's self-reported trustworthiness, minted at REDUCE and carried on the
# value so analysis can't silently read a biased number as clean:
#   - co_biased     : closed-loop tail latency under-samples slow responses (a trial-level
#                     property of model=closed) → don't treat as a strict SLO tail.
#   - high_drop     : open-loop max_inflight shed real load → percentiles understate reality.
#   - few_samples   : the slice has too few observations for stable percentiles.
#   - stale         : a probe missed enough ticks that the series is patchy.
#   - counter_reset : a counter went backwards mid-trial (pod restart / exporter reset);
#                     increase/rate use positive-delta accumulation but the window is split.
#   - probe_error   : the producing probe failed at least one tick — the series has holes,
#                     a flat-looking trend may be an artifact of missed samples.
Caveat = Literal["co_biased", "high_drop", "few_samples", "stale", "counter_reset", "probe_error"]


@dataclass(frozen=True)
class MetricFamily:
    """What a metric IS — its identity + type, declared ONCE (no concrete labels).

    Prometheus-faithful: ``name`` is the metric *family* (``top.cpu_m``); the
    dimensions that distinguish series within it (``{service="chat"}``) are labels
    carried by the series id, NOT by the family — so the report can pivot/group by
    them and the family's unit/value_kind/description are stored once, never copied
    per service. (The request side's facets are the same idea: a label on a
    request-side series.)

    ``description`` is the metric's human meaning, declared at its producer (a
    Probe's ``describe()``, ``REQUEST_DESCRIPTORS``, or a Workload's ``describe()``)
    and reused wherever it surfaces (report header tooltip, …).
    """

    name: str  # FAMILY: ttft_ms / top.mem_mi / metrics.req_total / request.duration_ms
    unit: str  # ms / MiB / count / tok
    side: MetricSide  # request (slices by facet) | resource (slices by service)
    value_kind: MetricValueKind  # decides legal stat
    source: str = "client"  # client / http / k8s / server — bottleneck grouping
    description: str = ""  # human meaning; surfaced in the report header tooltip
    labels: frozenset[str] = field(default_factory=frozenset)
    """Label names this family may emit; concrete values remain on each series."""


def series_id(name: str, labels: dict[str, str]) -> str:
    """Prometheus-style series id: ``name`` alone, or ``name{k="v",…}`` (sorted).

    The stat is still appended as ``.<stat>``, and label braces never contain that
    trailing ``.stat`` — so ``split_ref`` (rpartition on the last ``.``) keeps working
    on labeled ids unchanged."""
    if not labels:
        return name
    inner = ",".join(f'{k}="{labels[k]}"' for k in sorted(labels))
    return f"{name}{{{inner}}}"


@dataclass(frozen=True)
class CounterSummary:
    total: float
    rate: float | None = None
    increase: float | None = None
    caveats: frozenset[Caveat] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GaugeSummary:
    last: float
    mean: float | None = None
    peak: float | None = None
    caveats: frozenset[Caveat] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DistributionSummary:
    n: int
    mean: float
    p50: float
    p95: float
    p99: float
    caveats: frozenset[Caveat] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ScalarSummary:
    value: float
    caveats: frozenset[Caveat] = field(default_factory=frozenset)


MetricSummary = CounterSummary | GaugeSummary | DistributionSummary | ScalarSummary

# value_kind → the stats it legally exposes (the resolver/SLO validate against this).
# ``caveats`` is metadata on every summary, not an addressable stat.
LEGAL_STATS: dict[MetricValueKind, frozenset[str]] = {
    "counter": frozenset({"total", "rate", "increase"}),
    "gauge": frozenset({"last", "mean", "peak"}),
    "distribution": frozenset({"n", "mean", "p50", "p95", "p99"}),
    "scalar": frozenset({"value"}),
}


@dataclass(frozen=True)
class FacetDescriptor:
    """A facet a Workload declares it may stamp at runtime (so an SLO can gate it via
    a ``{facet="val"}`` label even though it isn't in the static Case mix). See
    Workload.describe_facets."""

    name: str
    values: list[str]


@dataclass(frozen=True)
class Missing:
    """A ``query`` result when a metric's slice has no value on a trial — a value,
    NOT ``None``, so a consumer must handle absence explicitly. An SLO maps it to a
    *skipped* check (never silently to pass); the report lists it as skipped.

    ``reason``: ``no_slice`` (the labeled slice was never produced — e.g. a facet
    value no request carried this trial), ``no_data`` (the family exists but the
    stat wasn't computed, e.g. a counter ``rate`` with <2 samples), ``too_few_samples``
    or ``probe_error``."""

    reason: Literal["no_slice", "no_data", "too_few_samples", "probe_error"]


# A resolved read: the value, or an explicit absence with its reason.
Read = float | Missing


def split_series(sid: str) -> tuple[str, dict[str, str]]:
    """Inverse of ``series_id``: ``'cpu_m{pod="a"}'`` → ``("cpu_m", {"pod": "a"})``,
    a bare name → ``(name, {})``. Unlike ``parse_ref`` there is no trailing ``.stat``
    here, so a dotted name is never mis-split."""
    if "{" not in sid:
        return sid, {}
    name, _, rest = sid.partition("{")
    inner, _, _ = rest.partition("}")
    labels: dict[str, str] = {}
    for pair in (p.strip() for p in inner.split(",") if p.strip()):
        k, _, v = pair.partition("=")
        labels[k.strip()] = v.strip().strip('"')
    return name, labels


def split_ref(ref: str) -> tuple[str, str]:
    """``"top.mem_mi.peak"`` → ``("top.mem_mi", "peak")`` (stat = last segment).

    Label braces never contain the trailing ``.stat`` so this works on labeled ids
    too: ``top.cpu_m{service="chat"}.peak`` → ``('top.cpu_m{service="chat"}', 'peak')``."""
    name, _, stat = ref.rpartition(".")
    if not name or not stat:
        raise ValueError(f"metric ref must be '<name>.<stat>', got {ref!r}")
    return name, stat


def parse_ref(ref: str) -> tuple[str, dict[str, str], str | None]:
    """Parse a metric ref into ``(name, labels, stat)``. Forms:

      - ``error_rate`` — undotted builtin alias (stat ``None``)
      - ``duration_ms.p99`` — dotted, overall
      - ``error_rate{difficulty="complex"}`` — alias + a label
      - ``duration_ms{difficulty="simple"}.p99`` / ``top.cpu_m{service="worker"}.peak``

    ``labels`` is the Prometheus ``{…}`` block; service / facet / stage are all just
    labels — the resolver routes on them. ``stat`` is the trailing ``.segment``."""
    if "{" in ref:
        name, _, rest = ref.partition("{")
        inner, _, tail = rest.partition("}")
        labels: dict[str, str] = {}
        for pair in (p.strip() for p in inner.split(",") if p.strip()):
            k, _, v = pair.partition("=")
            labels[k.strip()] = v.strip().strip('"')
        return name, labels, (tail[1:] if tail.startswith(".") else None)
    name, dot, stat = ref.rpartition(".")
    return (ref, {}, None) if not dot else (name, {}, stat)


def validate_ref(ref: str, registry: dict[str, MetricFamily]) -> None:
    """Static check (parse time): the metric family exists and the stat is legal for
    its ``value_kind``. ``registry`` is keyed by FAMILY name (labels are slice
    selectors, not identity), so a labeled ref is validated against its family.
    Raises ValueError — a perf gate must fail fast on a typo."""
    name, _labels, stat = parse_ref(ref)
    if stat is None:
        raise ValueError(f"metric ref must carry a stat '<name>.<stat>', got {ref!r}")
    fam = registry.get(name)
    if fam is None:
        raise ValueError(f"unknown metric {name!r} (known: {sorted(registry) or 'none'})")
    legal = LEGAL_STATS[fam.value_kind]
    if stat not in legal:
        raise ValueError(
            f"metric {name!r} is a {fam.value_kind}; stat {stat!r} illegal "
            f"(legal: {sorted(legal)})"
        )


def resolve(summaries: dict[str, MetricSummary], ref: str) -> float | None:
    """Runtime read: ``<name>{labels}.<stat>`` off a slice's summary map (keyed by
    series id). ``None`` when the series is absent or the stat value wasn't computed
    (e.g. a counter ``rate`` with <2 samples) → the store turns ``None`` into a
    ``Missing`` for the caller."""
    sid, stat = split_ref(ref)
    summary = summaries.get(sid)
    if summary is None:
        return None
    val = getattr(summary, stat, None)
    return float(val) if val is not None else None


def flatten(summaries: dict[str, MetricSummary]) -> dict[str, float]:
    """Expand each summary into ``{"<series>.<stat>": value}`` for its present stats
    — the flat view CSV/table columns render from (reducer in the stat, not the name).

    Stats are emitted in a STABLE (sorted) order: ``LEGAL_STATS`` is a frozenset, whose
    iteration order varies per process under hash randomization — so the report's
    column order would otherwise be non-deterministic run-to-run."""
    out: dict[str, float] = {}
    for name, s in summaries.items():
        for stat in sorted(LEGAL_STATS[_value_kind_of(s)]):
            v = getattr(s, stat, None)
            if v is not None:
                out[f"{name}.{stat}"] = float(v)
    return out


def _value_kind_of(s: MetricSummary) -> MetricValueKind:
    if isinstance(s, CounterSummary):
        return "counter"
    if isinstance(s, GaugeSummary):
        return "gauge"
    if isinstance(s, DistributionSummary):
        return "distribution"
    return "scalar"
