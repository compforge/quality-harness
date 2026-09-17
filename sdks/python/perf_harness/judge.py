"""Pure evaluation of recorded request Outcomes; independent of request execution."""

from collections.abc import Callable

from perf_harness.model import Outcome
from perf_harness.records import RequestEvaluation

Judge = Callable[[Outcome], RequestEvaluation]


def default_judge(outcome: Outcome) -> RequestEvaluation:
    if outcome.meta.get("exc"):
        return RequestEvaluation(False, str(outcome.meta["exc"]))
    if outcome.status is not None and 200 <= outcome.status < 300:
        return RequestEvaluation(True)
    return RequestEvaluation(
        False, str(outcome.status) if outcome.status is not None else "unknown"
    )


_REGISTRY: dict[str, Judge] = {"default": default_judge}


def register_judge(name: str, judge: Judge) -> None:
    """Register a pure business evaluation function for config ``judge: name``."""
    _REGISTRY[name] = judge


def build_judge(name: str) -> Judge:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown judge {name!r}; register it with register_judge") from None
