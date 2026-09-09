"""Top-level public API.

The canonical path is **judgment-as-data**: a structured ``common.Case`` (``input`` +
``judge.e2e.assert``) is run by ``engine`` over a protocol ``runner`` → ``verdict`` (CLI
``e2e run``). Cases are authored as ``case.yaml`` or as ``@case``/``@spec`` markers compiled by
``casegen``. Submodules:

- ``engine`` / ``assertion``: run a case + evaluate ``judge.e2e.assert`` → verdict
- ``cli`` / ``casegen``: ``e2e run`` (run) and ``casegen`` (marker → case.yaml)
- ``runner``: protocol adapters (JSON / SSE) producing ``Outcome``
- ``core``: config loading + profile gating
- ``judge.metric``: soft outcome metrics
- ``casegen``: NL authoring front-end — ``@case``/``@spec`` markers + AST discovery + the
  compiler that turns them into ``common.Case`` (``contract`` / ``discover`` / ``compiler``)

Dataset-driven evaluation lives in the sibling ``eval_harness`` package; see its README.
"""

from e2e_harness.casegen.contract import CASE_ID_PATTERN as CASE_ID_PATTERN
from e2e_harness.casegen.contract import Case as Case
from e2e_harness.casegen.contract import CaseSpec as CaseSpec
from e2e_harness.casegen.contract import case as case
from e2e_harness.casegen.contract import case_hash as case_hash
from e2e_harness.casegen.contract import get_cases as get_cases
from e2e_harness.casegen.contract import get_links as get_links
from e2e_harness.casegen.contract import get_rules as get_rules
from e2e_harness.casegen.contract import get_spec as get_spec
from e2e_harness.casegen.contract import get_spec_id as get_spec_id
from e2e_harness.casegen.contract import link as link
from e2e_harness.casegen.contract import load_cases_file as load_cases_file
from e2e_harness.casegen.contract import rule as rule
from e2e_harness.casegen.contract import spec as spec
from e2e_harness.casegen.discover import DiscoverConfig as DiscoverConfig
from e2e_harness.casegen.discover import DiscoveredCase as DiscoveredCase
from e2e_harness.casegen.discover import discover as discover
from e2e_harness.assertion import Assertion as Assertion
from e2e_harness.assertion import run_asserts as run_asserts
from e2e_harness.core.config import E2EConfig as E2EConfig
from e2e_harness.core.config import Experiment as Experiment
from e2e_harness.core.config import load_config as load_config
from e2e_harness.core.profile import require_capability as require_capability
from e2e_harness.core.profile import require_profile as require_profile
from e2e_harness.engine import run_case as run_case
from e2e_harness.engine import run_cases as run_cases
from e2e_harness.caserun import Budgets as Budgets
from e2e_harness.caserun import CaseRef as CaseRef
from e2e_harness.caserun import CasePlan as CasePlan
from e2e_harness.caserun import CaseRun as CaseRun
from e2e_harness.caserun import Fail as Fail
from e2e_harness.caserun import PhaseContext as PhaseContext
from e2e_harness.caserun import Skip as Skip
from e2e_harness.caserun import run_lifecycle as run_lifecycle
from e2e_harness.matrix import Variant as Variant
from e2e_harness.matrix import expand_matrix as expand_matrix
from e2e_harness.judge.metric import BaseMetric as BaseMetric
from e2e_harness.judge.metric import EventCountMetric as EventCountMetric
from e2e_harness.judge.metric import LatencyMetric as LatencyMetric
from e2e_harness.judge.metric import MetricKind as MetricKind
from e2e_harness.judge.metric import MetricRegistry as MetricRegistry
from e2e_harness.judge.metric import MetricResult as MetricResult
from e2e_harness.judge.metric import StatusMetric as StatusMetric
from e2e_harness.judge.metric import score_outcome as score_outcome
from e2e_harness.runner.async_base import AsyncBaseRunner as AsyncBaseRunner
from e2e_harness.runner.async_json_runner import AsyncJSONRunner as AsyncJSONRunner
from e2e_harness.runner.async_sse_runner import AsyncSSERunner as AsyncSSERunner
from e2e_harness.engine import E2ERun as E2ERun
from e2e_harness.engine import run_experiment as run_experiment
from e2e_harness.runner.base import BaseRunner as BaseRunner
from e2e_harness.runner.base import Outcome as Outcome
from e2e_harness.runner.base import Request as Request
from e2e_harness.reducer import E2EReducer as E2EReducer
from e2e_harness.runner.events import collect_text as collect_text
from e2e_harness.runner.events import count_events as count_events
from e2e_harness.runner.events import events as events
from e2e_harness.runner.events import find_event as find_event
from e2e_harness.runner.events import find_event_data as find_event_data
from e2e_harness.runner.json_runner import JSONRunner as JSONRunner
from e2e_harness.runner.sse_parser import SSEEvent as SSEEvent
from e2e_harness.runner.sse_parser import SSEParser as SSEParser
from e2e_harness.runner.sse_runner import SSERunner as SSERunner
