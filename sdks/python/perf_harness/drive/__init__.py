"""怎么压 — the load-generation side (extension point ①).

  - ``load``: the pressure *shape* — ``LoadProfile`` = model (open/closed) ×
    Schedule (intensity over time) × Pacing (closed think-time).
  - ``workload``: the per-service protocol adapter — ``fire(case)`` + ``judge``;
    real services register theirs via ``register_workload`` from their own repo.

The *what to fire* is a ``Case`` (common.case); this package is only how hard,
when, and over which protocol.
"""

from __future__ import annotations

from perf_harness.drive.load import (
    LoadModel as LoadModel,
)
from perf_harness.drive.load import (
    LoadProfile as LoadProfile,
)
from perf_harness.drive.load import (
    Pacing as Pacing,
)
from perf_harness.drive.load import (
    PacingKind as PacingKind,
)
from perf_harness.drive.load import (
    Schedule as Schedule,
)
from perf_harness.drive.load import (
    Stage as Stage,
)
from perf_harness.drive.workload import (
    FireContext as FireContext,
)
from perf_harness.drive.workload import (
    MockWorkload as MockWorkload,
)
from perf_harness.drive.workload import (
    TrialContext as TrialContext,
)
from perf_harness.drive.workload import (
    Workload as Workload,
)
from perf_harness.drive.workload import (
    build_workload as build_workload,
)
from perf_harness.drive.workload import (
    register_workload as register_workload,
)
from perf_harness.drive.workload import (
    stream_sse as stream_sse,
)
