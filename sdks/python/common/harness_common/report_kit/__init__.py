"""report_kit — neutral report document IR + HTML renderer, shared by eval/perf.

Business-free by design: it carries no Worksheet/Trial/metric concepts. Each SDK
maps its own pivot results into the IR (``Report``/``Section``/``Block``) and calls
``render_html``. See ``doc`` for the model and ``html`` for the renderer.
"""

# Explicit re-exports (``X as X``) so this stays the package's public surface without
# an ``__all__`` (see project convention) while keeping the linter quiet.
from harness_common.report_kit.doc import Block as Block
from harness_common.report_kit.doc import Chart as Chart
from harness_common.report_kit.doc import Heading as Heading
from harness_common.report_kit.doc import KV as KV
from harness_common.report_kit.doc import LineSeries as LineSeries
from harness_common.report_kit.doc import Prose as Prose
from harness_common.report_kit.doc import Report as Report
from harness_common.report_kit.doc import Section as Section
from harness_common.report_kit.doc import Table as Table
from harness_common.report_kit.html import render_html as render_html
