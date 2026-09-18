"""perf_harness — push a service under a resource budget and characterise it.

Three lines::

    一个 Experiment = ResourceProfile(资源档) × LoadPlan(负载档) 的网格;
    每个格子(ArmRun)由 Runner 发带 facet 的 Case、Probe 周期采样, 产出一张 metric 表;
    report / SLO / analyze 都是对这张表的查询。

Two things a service extends: a **Runner** (how to push) and a **Probe** (what
to observe). Everything else — load shaping, the resources × load sweep, the
report — the framework owns. The package is self-contained (no sibling-harness
import); k8s lives only in HelmDeployer + the K8s probes.

Import from the package root (``from perf_harness import Engine, Experiment``) —
the names below are the stable public API; internal module layout may change.
"""

from __future__ import annotations

from harness_common import Deployer as Deployer
from spec_case.model import Case as Case  # canonical case, shared across e2e / eval / perf

from perf_harness.config import load_experiment as load_experiment
from perf_harness.deploy import HelmDeployer as HelmDeployer
from perf_harness.drive.load import LoadPlan as LoadPlan
from perf_harness.drive.load import Stage as Stage
from perf_harness.drive.load import Warmup as Warmup
from perf_harness.drive.runner import ArmContext as ArmContext
from perf_harness.drive.runner import FireContext as FireContext
from perf_harness.drive.runner import MockRunner as MockRunner
from perf_harness.drive.runner import Runner as Runner
from perf_harness.drive.runner import build_runner as build_runner
from perf_harness.drive.runner import register_runner as register_runner
from perf_harness.drive.runner import stream_sse as stream_sse
from perf_harness.engine import Engine as Engine
from perf_harness.engine import Experiment as Experiment
from perf_harness.judge import Judge as Judge
from perf_harness.judge import default_judge as default_judge
from perf_harness.judge import register_judge as register_judge
from perf_harness.metric import CounterSummary as CounterSummary
from perf_harness.metric import DistributionSummary as DistributionSummary
from perf_harness.metric import FacetDescriptor as FacetDescriptor
from perf_harness.metric import GaugeSummary as GaugeSummary
from perf_harness.metric import MetricFamily as MetricFamily
from perf_harness.metric import Missing as Missing
from perf_harness.metric import ScalarSummary as ScalarSummary
from perf_harness.metric.store import MetricStore as MetricStore
from perf_harness.model import Arm as Arm
from perf_harness.model import ArmRun as ArmRun
from perf_harness.model import ArmStop as ArmStop
from perf_harness.model import Deployment as Deployment
from perf_harness.model import Environment as Environment
from perf_harness.model import Outcome as Outcome
from perf_harness.model import RequestStats as RequestStats
from perf_harness.model import ResourceProfile as ResourceProfile
from perf_harness.model import Run as Run
from perf_harness.model import Sample as Sample
from perf_harness.model import Series as Series
from perf_harness.model import Service as Service
from perf_harness.model import SloAssertion as SloAssertion
from perf_harness.model import SloCheck as SloCheck
from perf_harness.model import StopSnapshot as StopSnapshot
from perf_harness.model import Window as Window
from perf_harness.model import WindowSelector as WindowSelector
from perf_harness.model import make_run_id as make_run_id
from perf_harness.observe import ClientProbe as ClientProbe
from perf_harness.observe import FamilySpec as FamilySpec
from perf_harness.observe import KubectlTopProbe as KubectlTopProbe
from perf_harness.observe import PerWorkerRSSProbe as PerWorkerRSSProbe
from perf_harness.observe import PodCountProbe as PodCountProbe
from perf_harness.observe import Probe as Probe
from perf_harness.observe import ProbeConfig as ProbeConfig
from perf_harness.observe import ProbeContext as ProbeContext
from perf_harness.observe import PrometheusProbe as PrometheusProbe
from perf_harness.observe import PrometheusQuery as PrometheusQuery
from perf_harness.observe import ResourceLimitsProbe as ResourceLimitsProbe
from perf_harness.observe import RestartProbe as RestartProbe
from perf_harness.observe import register_probe as register_probe
from perf_harness.observe.prometheus_query import PrometheusQueryProbe as PrometheusQueryProbe
from perf_harness.records import RequestEvaluation as RequestEvaluation
from perf_harness.records import RequestRecord as RequestRecord
from perf_harness.report import write_report as write_report
from perf_harness.report import write_run as write_run
from perf_harness.runio import PerfReducer as PerfReducer
from perf_harness.runio import load_run as load_run
from perf_harness.runio import write_run_data as write_run_data
