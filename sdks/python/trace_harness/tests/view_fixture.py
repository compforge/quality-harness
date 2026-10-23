from trace_harness import AnalysisContext, Measurements
from trace_harness.view.build import build_view
from trace_harness.view.engine import render as render_display
from trace_harness.view.engine import render_md
from trace_harness.view.measurements import measurements_md
from trace_harness.view.render import render


def prepared_html(
    context, findings=None, *, measurements=None, facet_registry=None, agent_run_ir=None
):
    analysis = AnalysisContext(
        context, measurements or Measurements(), findings or {}, agent_runs=agent_run_ir
    )
    views = [build_view(analysis, {"scope": "trace"}, facets=facet_registry)]
    if agent_run_ir:
        views.append(
            build_view(analysis, {"scope": "trace", "view": "agent"}, facets=facet_registry)
        )
    return render(views, format="html")


def report_html(harness, context, findings=None, *, measurements=None):
    return prepared_html(
        context,
        findings,
        measurements=harness.visible_measurements(context, measurements) if measurements else None,
        facet_registry=harness.facets,
        agent_run_ir=harness.extract_agent_runs(context),
    )


def display(harness, context, findings=None):
    return render_display(context.tree(), findings, registry=harness.facets)


def markdown(harness, context, findings=None, *, measurements=None):
    return render_md(context, findings, registry=harness.facets) + measurements_md(
        context, harness.visible_measurements(context, measurements) if measurements else None
    )
