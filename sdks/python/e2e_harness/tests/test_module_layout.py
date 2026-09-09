"""Verify the e2e_harness public layout: the judgment-as-data canonical surface resolves, and
the retired legacy api-mode (scaffold / BaseCase / AssertJudge / …) is gone."""

from __future__ import annotations

import pytest


def test_top_level_aggregates_canonical_surface():
    """``from e2e_harness import X`` resolves the judgment-as-data path + marker front-end."""
    from e2e_harness import (
        Assertion,
        Budgets,
        Case,
        CaseRef,
        CasePlan,
        CaseRun,
        CaseSpec,
        DiscoverConfig,
        DiscoveredCase,
        JSONRunner,
        Outcome,
        Request,
        SSERunner,
        Variant,
        case,
        case_hash,
        discover,
        run_asserts,
        run_case,
        run_cases,
        run_lifecycle,
        spec,
    )

    assert all(
        x is not None
        for x in [
            Case,
            CaseRef,
            CasePlan,
            CaseRun,
            Budgets,
            Variant,
            CaseSpec,
            case,
            spec,
            case_hash,  # marker front-end (contract)
            discover,
            DiscoverConfig,
            DiscoveredCase,  # AST discovery (casegen reuses)
            run_case,
            run_cases,
            run_lifecycle,
            Assertion,
            run_asserts,  # engine + judgment-as-data
            JSONRunner,
            SSERunner,
            Outcome,
            Request,  # protocol adapters
        ]
    )


def test_casegen_subpackage_is_authoring_frontend():
    """``e2e_harness.casegen`` = the NL authoring front-end: @case/@spec markers + AST discovery
    + the compiler that turns them into ``common.Case`` (no test classes)."""
    from e2e_harness.casegen import (
        Case,
        CaseSpec,
        DraftCompiler,
        case,
        compile_cases,
        discover,
        spec,
    )

    assert all(
        x is not None
        for x in [Case, CaseSpec, case, spec, discover, compile_cases, DraftCompiler]
    )


def test_judge_metric_subpackage_layout():
    from e2e_harness.judge.metric import BaseMetric, LatencyMetric, MetricRegistry
    from e2e_harness.judge.metric.base import BaseMetric as BaseMetricDirect

    assert (
        BaseMetric is BaseMetricDirect
        and LatencyMetric is not None
        and MetricRegistry is not None
    )


def test_retired_legacy_api_mode_is_gone():
    """The old api-mode (pytest-body model) was removed — judgment is data now, not a test body.
    Its whole ``e2e_harness.api`` package is gone too; the authoring front-end now lives under
    ``e2e_harness.casegen`` (markers + discovery folded in with the compiler)."""
    for legacy in [
        "e2e_harness.api",  # the retired api-mode package itself (folded into casegen)
        "e2e_harness.api.scaffold",
        "e2e_harness.api.meta_block",
        "e2e_harness.api.case",  # BaseCase
        "e2e_harness.api.case_rpc",  # RPCCase
        "e2e_harness.api.agent_case",  # AgentCase
        "e2e_harness.judge.assert_judge",  # AssertJudge
        "e2e_harness.api.verdict",
        "e2e_harness.pytest_plugin",
        "e2e_harness.conftest_helpers",
    ]:
        with pytest.raises(ImportError):
            __import__(legacy)


def test_dataset_eval_lives_in_eval_harness():
    """Dataset-driven eval lives in the sibling ``eval_harness``; the LLM client in ``common.llm``."""
    for legacy in [
        "e2e_harness.eval",
        "e2e_harness.llm",
        "e2e_harness.judge.metric.llm_judge",
    ]:
        with pytest.raises(ImportError):
            __import__(legacy)
