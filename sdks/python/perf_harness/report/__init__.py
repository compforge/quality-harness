"""Report — the VIEW layer: render a Run's model into md/html/csv artifacts.

Pure downstream of the model layer (``run.json`` / ``MetricStore``): re-rendering
never re-presses anything, and a layout change applies to old runs (``cli report``).
``render`` holds the writers and section builders; ``palette`` pins display colors
(display policy lives here, never on the model).
"""

from __future__ import annotations

from perf_harness.report.palette import family_color as family_color
from perf_harness.report.render import write_report as write_report
from perf_harness.report.render import write_run as write_run
