"""Neutral report document IR — the contract between an SDK's pivots and a renderer.

``report_kit`` is deliberately business-free: it knows nothing about Worksheets,
Trials, metrics or facets. eval_harness / perf_harness each keep their own pivot
logic and map the *result* into this shape (Report → Section → Block). A renderer
(``render_html``) then walks the IR. This is the one place the two SDKs share — not
each other's code, but a common low-level document model, the same way they already
share ``spec/``'s conventions.

A document is an ordered list of Sections; a Section is an ordered list of Blocks;
a Block is one of: Prose (free markdown), KV (a summary list), Table (header + rows,
with optional row highlight / score-column heat / default sort), or Chart (a line
chart, the one thing markdown can't carry). Everything is plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Prose:
    """A free-text paragraph (markdown-ish; rendered as a `<p>`, light inline only)."""

    text: str


@dataclass
class Heading:
    """A sub-heading INSIDE a section — opens a named sub-part that runs until the
    next Heading (e.g. one per service). The HTML renderer makes each sub-part its
    own nested ``<details open>`` (independently collapsible, h3-styled summary);
    a markdown twin would render ``###``."""

    text: str


@dataclass
class KV:
    """A summary list — label → value pairs, rendered as a definition list."""

    items: list[tuple[str, str]]


@dataclass
class Table:
    """A header + string rows. Cells are pre-formatted strings (the SDK owns formatting).

    ``highlight`` maps a row index to a CSS class hint (e.g. ``"winner"`` / ``"knee"``)
    so the renderer can call attention to it. ``heat_cols`` lists column indices whose
    cells hold a score in ``[0, 1]`` and should get a red→green background heat (cells
    that don't parse as a float are left uncolored). ``sort_default`` is a
    ``(col_index, "asc"|"desc")`` hint applied on load; all columns stay click-sortable.
    ``col_tips`` maps a column *label* to hover-tooltip text (e.g. a metric description),
    rendered as a native ``title=`` on that header. ``tree_col`` marks one column as
    holding tree-drawn text (box-drawing prefixes like ``│  ├─``): the renderer must
    keep its whitespace, left-align it and use a monospace font — without this, HTML
    whitespace collapsing + proportional fonts flatten the hierarchy (a call stack
    reads as an unindented flat list).
    """

    columns: list[str]
    rows: list[list[str]]
    highlight: dict[int, str] = field(default_factory=dict)
    heat_cols: list[int] = field(default_factory=list)
    sort_default: tuple[int, str] | None = None
    # column label → hover-tooltip text; columns absent from the map get no tooltip.
    col_tips: dict[str, str] = field(default_factory=dict)
    # index of the column whose cells are tree-drawn text; None → no tree column.
    tree_col: int | None = None


@dataclass
class LineSeries:
    """One named signal in a line chart: a sequence of ``(x, y)`` points.

    ``color`` (optional, e.g. ``"#d62728"``) pins the line's color so the SAME
    signal reads the same on every chart (reference lines especially); empty →
    the charting lib's palette rotation. ``tip`` (optional) is the signal's human
    meaning — the renderer shows it when hovering the legend item (series names
    are addressing ids like ``metrics.sse_ok{…}``; the tip says what that IS)."""

    name: str
    points: list[tuple[float | str, float]]
    color: str = ""
    tip: str = ""


@dataclass
class Chart:
    """A line chart — multiple same-scale signals over a shared x axis.

    The renderer draws this with a charting lib (CDN); it is the one block kind that
    needs JS, so a document with no Chart renders fully offline. Keep series on the
    chart same-scale (group by unit) — mixing cpu% and bytes on one axis misleads.

    ``right_series`` (optional) draws on a SECOND y axis (``y2_label``), dashed — for
    overlaying a different-unit context signal (e.g. load pressure) on a resource
    chart. Use sparingly: dual axes invite false correlation reads, so the right side
    should be context, not a co-equal measurement.

    ``x_kind="time"`` accepts ISO timestamps for reports sampled at irregular times;
    value axes remain available for elapsed or numeric samples.
    """

    title: str
    series: list[LineSeries]
    x_label: str = "t (s)"
    x_kind: Literal["value", "time"] = "value"
    y_label: str = ""
    right_series: list[LineSeries] = field(default_factory=list)
    y2_label: str = ""


Block = Prose | Heading | KV | Table | Chart


@dataclass
class Section:
    heading: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Report:
    """A whole document: a title, a few header key/values, and ordered sections."""

    title: str
    meta: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)

    def has_charts(self) -> bool:
        return any(isinstance(b, Chart) for s in self.sections for b in s.blocks)
