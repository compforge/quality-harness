"""TraceHarness contributions are explicit and isolated per harness instance."""

from __future__ import annotations

from pathlib import Path

from trace_harness import FactTransform, TraceContributions, TraceHarness
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file
from trace_harness.kinds import genai
from trace_harness.model.node import Finding
from trace_harness.view.facet import DefaultFacet

FIXTURE = Path(__file__).parents[4] / "conformance" / "trace" / "fixtures" / "genai-basic.jsonl"


def _scope_detector(node, ctx):
    if node.facts.get("scope_marker") == "alpha":
        return [Finding(node.node_id, "scope:alpha", "info")]
    return []


class _AlphaModelFacet(DefaultFacet):
    """Notebook-like view rule: keep model-call HTTP children visible."""

    priority = 100

    def match(self, node):
        return node.kind == "model-call"


def _displayed_node_ids(roots) -> set[str]:
    ids: set[str] = set()
    stack = list(roots)
    while stack:
        display = stack.pop()
        if display.kind:
            ids.update(display.node_ids)
        stack.extend(display.children)
    return ids


def test_contributions_do_not_leak_between_harnesses():
    alpha = TraceHarness(
        TraceContributions(
            specs=tuple(genai.specs()),
            transforms=(
                FactTransform(
                    ("scope_marker",),
                    lambda node: node.kind == "agent",
                    lambda node, ctx: {"scope_marker": "alpha"},
                ),
                FactTransform(
                    ("scope_action",),
                    lambda node: node.kind == "agent",
                    lambda node, ctx: {"scope_action": "alpha-action"},
                ),
            ),
            detectors=(_scope_detector,),
            facets=(_AlphaModelFacet(),),
        )
    )
    plain = TraceHarness(TraceContributions(specs=tuple(genai.specs())))

    alpha_context = alpha.assemble(load_jaeger_file(FIXTURE))
    plain_context = plain.assemble(load_jaeger_file(FIXTURE))
    alpha_agent = next(node for node in alpha_context.nodes if node.kind == "agent")
    plain_agent = next(node for node in plain_context.nodes if node.kind == "agent")

    alpha.transform_all(alpha_context, "scope_marker")
    assert alpha_agent.facts["scope_marker"] == "alpha"
    assert "scope_marker" not in plain_agent.facts
    assert alpha.transform(alpha_agent, alpha_context, "scope_action") == {
        "scope_action": "alpha-action"
    }
    assert plain.transform(plain_agent, plain_context, "scope_action") == {}
    alpha_findings = alpha.diagnose(alpha_context)
    plain_findings = plain.diagnose(plain_context)
    assert any(finding.source == "scope:alpha" for finding in alpha_findings[alpha_agent.node_id])
    assert not any(
        finding.source == "scope:alpha"
        for findings in plain_findings.values()
        for finding in findings
    )
    success_http = next(
        node
        for node in alpha_context.nodes
        if node.kind == "http" and node.facts.get("status") == 200
    )
    assert success_http.node_id in _displayed_node_ids(
        alpha.render_display(alpha_context, alpha_findings)
    )
    assert success_http.node_id not in _displayed_node_ids(
        plain.render_display(plain_context, plain_findings)
    )
    assert "alpha-action" in alpha.render_interactive(alpha_context, alpha_findings)
    assert "alpha-action" not in plain.render_interactive(plain_context, plain_findings)


def test_probe_side_effect_remains_explicit(tmp_path: Path):
    harness = TraceHarness(TraceContributions(specs=tuple(genai.specs())))
    context = harness.assemble(load_jaeger_file(FIXTURE))
    context.evidence_dir = tmp_path / "evidence"

    harness.diagnose(context)
    assert not context.evidence_dir.exists()

    harness.diagnose(context, probes=True)
    assert context.evidence_dir.exists()
