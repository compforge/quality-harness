"""analysis — deterministic observations over a run, for humans and agents alike.

``analyze_run(run_dir)`` loads the model layer (``runio.load_run``) and walks four
lenses — capacity / resource / latency / validity — each emitting ``Observation``s
(one-line title + machine-readable evidence). ``render_text`` groups them flags-
first per lens. CLI: ``python -m perf_harness.cli analyze <run_dir>``.

The lenses compute the mechanical part of a perf analysis (ratios, slopes,
headroom, adequacy, self-checks); interpretation stays with the reader.
"""

from __future__ import annotations

from perf_harness.analysis import capacity, latency, resource, validity
from perf_harness.analysis.base import Observation as Observation
from perf_harness.metric.store import MetricStore
from perf_harness.model import Run
from perf_harness.runio import load_run

_LENSES = (
    ("capacity", capacity.analyze),
    ("resource", resource.analyze),
    ("latency", latency.analyze),
    ("validity", validity.analyze),
)

_LENS_TITLE = {
    "capacity": "容量与扩展性",
    "resource": "资源画像（usage vs request/limit）",
    "latency": "延迟形态",
    "validity": "本次压测的有效性",
}


def analyze(run: Run) -> list[Observation]:
    """Run every lens over the Run → all observations (lens order, flags mixed in)."""
    store = MetricStore(run.trials)
    out: list[Observation] = []
    for _, lens in _LENSES:
        out.extend(lens(run, store))
    return out


def analyze_run(run_dir: str) -> list[Observation]:
    """The one-call entry: run dir → observations (model layer in, no HTML parsing)."""
    return analyze(load_run(run_dir))


def render_text(observations: list[Observation]) -> str:
    """Group by lens, flags first — a digest a human skims and an agent quotes."""
    lines: list[str] = []
    for lens, _ in _LENSES:
        obs = [o for o in observations if o.analyzer == lens]
        if not obs:
            continue
        lines.append(f"## {_LENS_TITLE.get(lens, lens)}")
        for o in sorted(obs, key=lambda o: o.kind != "flag"):  # flags first
            mark = "⚠" if o.kind == "flag" else "·"
            lines.append(f"{mark} {o.title}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
