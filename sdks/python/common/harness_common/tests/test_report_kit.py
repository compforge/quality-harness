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
