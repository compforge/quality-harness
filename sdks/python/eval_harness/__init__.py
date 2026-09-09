"""eval_harness — Experiment-first, table-centric evaluation harness.

Spine (see plan / README): one evalset, run through one-or-more **Arm**s
(comparison arms), produces a single in-memory **Worksheet** (the big table)
that a reconciler fills cell-by-cell, and a report renders by pure pivot.

    evalset ──( solver 补 answer / scorer 补 metric )──▶ Worksheet ──pivot──▶ report

This package is intentionally self-contained (no e2e_harness import) so the
table-centric model stays clean; protocol/adapter reuse is a later concern.

The names below are the stable public API — import from ``eval_harness`` directly
(``from eval_harness import Experiment, Solver, run_experiment``) rather than from
internal module paths, so internal layout can change without breaking consumers.
"""

from harness_common.llm import ChatResult as ChatResult
from harness_common.llm import LLMClient as LLMClient
from harness_common.llm import LLMConfig as LLMConfig
from spec_case.model import (
    Case as Case,  # the canonical input unit (was the eval-private EvalCase)
)

from eval_harness.config import load_experiment as load_experiment
from eval_harness.engine import resolve_weights as resolve_weights
from eval_harness.engine import run_experiment as run_experiment
from eval_harness.engine import teardown_provisions as teardown_provisions
from eval_harness.engine import write_reports as write_reports
from eval_harness.ingest import Importer as Importer
from eval_harness.ingest import cached_download as cached_download
from eval_harness.ingest import dump_cases_yaml as dump_cases_yaml
from eval_harness.ingest import dump_evalset as dump_evalset
from eval_harness.ingest import fetch_json as fetch_json
from eval_harness.ingest import slug as slug
from eval_harness.metric.base import BaseMetric as BaseMetric
from eval_harness.metric.llm_judge import LLMJudge as LLMJudge
from eval_harness.metric.registry import register as register
from eval_harness.metric.registry import resolve as resolve
from eval_harness.model.evalset import EvalSet as EvalSet
from eval_harness.model.evalset import EvalView as EvalView
from eval_harness.model.evalset import FacetSchema as FacetSchema
from eval_harness.model.evalset import FacetSpec as FacetSpec
from eval_harness.model.evalset import SourceRecord as SourceRecord
from eval_harness.model.evalset import eval_view as eval_view
from eval_harness.model.experiment import Arm as Arm
from eval_harness.model.experiment import Experiment as Experiment
from eval_harness.model.experiment import LLMSpec as LLMSpec
from eval_harness.model.experiment import Service as Service
from eval_harness.model.sample import MetricResult as MetricResult
from eval_harness.model.sample import Sample as Sample
from eval_harness.schedule.ratelimit import GateRegistry as GateRegistry
from eval_harness.schedule.reconcile import Provisioner as Provisioner
from eval_harness.schedule.reconcile import Solver as Solver
from eval_harness.schedule.reconcile import SolveResult as SolveResult
from eval_harness.worksheet.worksheet import Row as Row
from eval_harness.worksheet.worksheet import Worksheet as Worksheet
