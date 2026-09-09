"""Internal predicates over ATIF trajectory steps."""

from atif import Step

from trajectory_harness.model import step_name, step_operation


def is_compact_step(step: Step) -> bool:
    operation = step_operation(step).lower().replace("_", ".")
    name = step_name(step).lower().replace("_", ".")
    return operation in {"compact", "context.compact"} or name in {
        "compact",
        "context.compact",
    }
