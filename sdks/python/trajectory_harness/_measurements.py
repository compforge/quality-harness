"""Internal queries over derived trajectory measurements."""

from __future__ import annotations

from trajectory_harness.measure import MeasurementResult, Measurements


def measured_result(
    measurements: Measurements, measurer_id: str
) -> MeasurementResult | None:
    """Return one usable result for a measurer, leaving health handling to the run."""

    return next(
        (
            result
            for result in measurements
            if result.measurer_id == measurer_id and result.status == "measured"
        ),
        None,
    )


def numeric_measurement(
    measurements: Measurements, measurer_id: str, name: str
) -> float | None:
    result = measured_result(measurements, measurer_id)
    if result is None:
        return None
    value = result.measurements.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
