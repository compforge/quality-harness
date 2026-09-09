"""The metric waist — the one table everything narrows through.

Producers (drive/observe) write into it, consumers (report / SLO / analysis)
read from it; neither knows the other. Three submodules:

  - ``family``: the pure model — ``MetricFamily`` (identity + side + value_kind),
    the typed summary union, ``Missing``, and the ``<name>{labels}.<stat>``
    addressing helpers. Stdlib-only; never imports model.py.
  - ``store``: ``MetricStore``, the one addressable read face over a run's trials
    (+ the builtin request-side family descriptors).
  - ``reduce``: the minting point — raw observations → typed summaries + caveats.

This ``__init__`` re-exports only the pure ``family`` layer (model.py imports it,
so pulling ``store``/``reduce`` — which import model.py — in here would cycle);
import those as submodules (``from perf_harness.metric.store import MetricStore``).
"""

from __future__ import annotations

from perf_harness.metric.family import (
    LEGAL_STATS as LEGAL_STATS,
)
from perf_harness.metric.family import (
    Caveat as Caveat,
)
from perf_harness.metric.family import (
    CounterSummary as CounterSummary,
)
from perf_harness.metric.family import (
    DistributionSummary as DistributionSummary,
)
from perf_harness.metric.family import (
    FacetDescriptor as FacetDescriptor,
)
from perf_harness.metric.family import (
    GaugeSummary as GaugeSummary,
)
from perf_harness.metric.family import (
    MetricFamily as MetricFamily,
)
from perf_harness.metric.family import (
    MetricSide as MetricSide,
)
from perf_harness.metric.family import (
    MetricSummary as MetricSummary,
)
from perf_harness.metric.family import (
    MetricValueKind as MetricValueKind,
)
from perf_harness.metric.family import (
    Missing as Missing,
)
from perf_harness.metric.family import (
    Read as Read,
)
from perf_harness.metric.family import (
    ScalarSummary as ScalarSummary,
)
from perf_harness.metric.family import (
    flatten as flatten,
)
from perf_harness.metric.family import (
    parse_ref as parse_ref,
)
from perf_harness.metric.family import (
    resolve as resolve,
)
from perf_harness.metric.family import (
    series_id as series_id,
)
from perf_harness.metric.family import (
    split_ref as split_ref,
)
from perf_harness.metric.family import (
    split_series as split_series,
)
from perf_harness.metric.family import (
    validate_ref as validate_ref,
)
