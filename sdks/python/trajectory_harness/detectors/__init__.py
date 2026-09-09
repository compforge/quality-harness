"""Built-in common trajectory detectors."""

from trajectory_harness.detectors.cache_retention_bloat import (
    CacheRetentionBloatDetector as CacheRetentionBloatDetector,
)
from trajectory_harness.detectors.context_bloat import (
    ContextBloatWithoutCompactDetector as ContextBloatWithoutCompactDetector,
)
from trajectory_harness.detectors.oversized_tool_observation import (
    OversizedToolObservationDetector as OversizedToolObservationDetector,
)
from trajectory_harness.detectors.post_compact_refetch import (
    PostCompactRefetchDetector as PostCompactRefetchDetector,
)
from trajectory_harness.detectors.repeated_tool_call import (
    RepeatedToolCallDetector as RepeatedToolCallDetector,
)
from trajectory_harness.detectors.retry_loop import (
    RetryLoopDetector as RetryLoopDetector,
)
from trajectory_harness.detectors.short_decision_churn import (
    ShortDecisionChurnDetector as ShortDecisionChurnDetector,
)
from trajectory_harness.detectors.unchanged_tool_retry import (
    UnchangedToolRetryDetector as UnchangedToolRetryDetector,
)
