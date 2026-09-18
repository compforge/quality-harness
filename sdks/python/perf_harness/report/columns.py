"""Explicit report columns read persisted facts through MetricStore."""

from perf_harness.metric import Missing
from perf_harness.metric.store import MetricStore
from perf_harness.model import ArmRun, ReportColumn


def selected_cells(run: ArmRun, columns: list[ReportColumn]) -> list[str]:
    store = MetricStore([run])
    cells = []
    for column in columns:
        windows = [window for window in run.windows if column.window.matches(window)]
        values = []
        for window in windows:
            value = store.query(run, column.metric, window)
            text = f"missing ({value.reason})" if isinstance(value, Missing) else f"{value:.4g}"
            if not window.complete:
                text += " (incomplete)"
            values.append(f"{window.id}: {text}" if len(windows) > 1 else text)
        cells.append("; ".join(values) if values else "missing (no window)")
    return cells


def cases_cell(run: ArmRun) -> str:
    return ", ".join(sorted(run.measurement.by_case)) or "—"
