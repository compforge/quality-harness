"""corpus —— 跨 trace 分析层：N 个 TraceContext → 三张扁平特征表 → 语料算子。

三表是 Canopy「列式数据集」的对等物——node→facts 那一步（KindSpec.build）已把业务字段抽成
命名列，所以 corpus 拿到的就是干净特征列，group/aggregate/diff 全是列运算、不回头碰 raw、
不认识任何 kind 的业务语义。这也是跨 trace 层"没有特权结构"的体现：行是 node/finding/trace 摘要。

2a（本模块）是纯 Python 分析核心（零新依赖）：三表构建 + 三算子。parquet 持久化 + report_kit
报告 + experiment/CLI 随 2b。
"""

from __future__ import annotations

from trace_harness.corpus.experiment import run_experiment as run_experiment
from trace_harness.corpus.operators import diff_runs as diff_runs
from trace_harness.corpus.operators import error_signatures as error_signatures
from trace_harness.corpus.operators import fleet_outliers as fleet_outliers
from trace_harness.corpus.operators import pattern_rates as pattern_rates
from trace_harness.corpus.report import build_report as build_report
from trace_harness.corpus.report import write_report as write_report
from trace_harness.corpus.store import read_tables as read_tables
from trace_harness.corpus.store import write_tables as write_tables
from trace_harness.corpus.tables import CorpusTables as CorpusTables
from trace_harness.corpus.tables import build_tables as build_tables
