from harness_common.report_kit import Chart, LineSeries, Report, Section, render_html


def test_time_chart_renders_axis_and_iso_timestamps():
    html = render_html(
        Report(
            title="trend",
            sections=[
                Section(
                    heading="metrics",
                    blocks=[
                        Chart(
                            title="pass rate",
                            x_kind="time",
                            series=[
                                LineSeries(
                                    name="reviews/unit",
                                    points=[
                                        ("2026-08-01T10:00:00+00:00", 0.8),
                                        ("2026-08-03T15:30:00+00:00", 0.9),
                                    ],
                                )
                            ],
                        )
                    ],
                )
            ],
        )
    )

    assert '"type": "time"' in html
    assert "2026-08-01T10:00:00+00:00" in html
    assert "2026-08-03T15:30:00+00:00" in html


def test_graph_is_offline_and_untrusted_labels_cannot_close_script():
    from harness_common.report_kit import GraphFrame, GraphNode, GraphView

    malicious = "</script><img src=x onerror=alert(1)>"
    view = GraphView("calls", [GraphFrame("window", [GraphNode("id", malicious)], [])])
    rendered = render_html(Report("test", sections=[Section("graph", [view])]))
    assert "<script src=" not in rendered
    assert malicious not in rendered
    assert "cytoscape" in rendered and "rkGraph(" in rendered
    assert "\\u003c/script>" in rendered
    assert "textContent" in rendered


def test_tabs_include_nested_assets_and_keep_only_first_panel_visible():
    from harness_common.report_kit import GraphFrame, GraphView, Prose, ReportTab

    report = Report(
        "tabs",
        tabs=[
            ReportTab("Report", [Section("summary", [Prose("original report")])]),
            ReportTab(
                "Callgraph <view>",
                [
                    Section(
                        "graph", [GraphView("calls", [GraphFrame("window", [], [])])]
                    )
                ],
            ),
            ReportTab("Chart", [Section("trend", [Chart("rate", [])])]),
        ],
    )
    rendered = render_html(report)
    assert report.has_graphs() and report.has_charts()
    assert 'role="tab" id="rk-tab-0"' in rendered
    assert 'aria-selected="true" tabindex="0">Report</button>' in rendered
    assert 'aria-labelledby="rk-tab-0"><details' in rendered
    assert 'aria-labelledby="rk-tab-1" hidden><details' in rendered
    assert 'aria-labelledby="rk-tab-2" hidden><details' in rendered
    assert "Callgraph &lt;view&gt;" in rendered
    assert "echarts.init" in rendered and "function rkGraph" in rendered
    assert 'type="range" min="0" max="100"' in rendered
    assert "original report" in rendered


def test_report_without_tabs_keeps_flat_sections():
    rendered = render_html(Report("flat", sections=[Section("summary")]))
    assert 'role="tablist"' not in rendered
    assert "<details open><summary>summary</summary>" in rendered


def test_graph_frame_tables_are_escaped_collapsed_and_not_duplicated_in_js():
    from harness_common.report_kit import GraphFrame, GraphView, Table

    view = GraphView(
        "calls",
        [
            GraphFrame("measurement", [], [], Table(["window"], [["<measurement>"]])),
            GraphFrame("observation", [], [], Table(["window"], [["observation"]])),
            GraphFrame("empty", [], []),
        ],
        table_title="指标明细",
    )
    rendered = render_html(Report("test", sections=[Section("graph", [view])]))
    assert '<details class="graph-table"><summary>指标明细</summary>' in rendered
    assert '<div data-graph-frame="0"><table' in rendered
    assert '<div data-graph-frame="1" hidden><table' in rendered
    assert (
        '<div data-graph-frame="2" hidden><p>No observations in this view.</p>'
        in rendered
    )
    assert "&lt;measurement&gt;" in rendered
    assert "<measurement>" not in rendered
    assert (
        '"table":' not in rendered
    )  # Tables are rendered once, not copied into JS payload.


def test_wide_tab_uses_viewport_layout_without_widening_default_report():
    from harness_common.report_kit import ReportTab

    rendered = render_html(
        Report(
            "layout",
            tabs=[
                ReportTab("Report", [Section("summary")]),
                ReportTab("Callgraph", [Section("graph")], wide=True),
            ],
        )
    )
    assert 'aria-labelledby="rk-tab-0" data-wide=' not in rendered
    assert 'aria-labelledby="rk-tab-1" data-wide="true" hidden' in rendered
    assert "height:max(600px, 78vh)" in rendered
