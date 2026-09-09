"""trace_harness —— e2e-harness 的第四个 SDK：trace/span 分析框架。

前三个 harness 把系统当黑盒（发请求看响应），trace_harness 开盒：消费请求留下的遥测
（OTel/Jaeger span），回答"链路内部哪一层先反常"。设计见 docs/trace-harness.md。

核心立场（与 Canopy 同构）：raw span 是物理事实，**逻辑事件 node 是分析本体**，
特征列 facts/findings 是分析底座；"树"只是看单条 trace 时的视图，不是中心对象。

公共 API 从包根导入（``from trace_harness import build_context, render_text``）；
内部模块布局可能变动，包根名是稳定面。
"""

from __future__ import annotations

from trace_harness.harness import TraceContributions as TraceContributions
from trace_harness.harness import TraceHarness as TraceHarness
from trace_harness.harness import contributions_from_specs as contributions_from_specs
from trace_harness.harness import merge_trace_contributions as merge_trace_contributions
from trace_harness.ingest.assemble import assemble as assemble
from trace_harness.ingest.load import build_context as build_context
from trace_harness.ingest.sources.jaeger_file import load_jaeger_file as load_jaeger_file
from trace_harness.model.agent import AGENT_RUN_SCHEMA as AGENT_RUN_SCHEMA
from trace_harness.model.agent import AgentRun as AgentRun
from trace_harness.model.agent import AgentRunIR as AgentRunIR
from trace_harness.model.agent import AgentRunItem as AgentRunItem
from trace_harness.model.agent import AgentTurn as AgentTurn
from trace_harness.model.agent import ModelCall as ModelCall
from trace_harness.model.agent import Operation as Operation
from trace_harness.model.agent import ToolCall as ToolCall
from trace_harness.model.agent import TurnItem as TurnItem
from trace_harness.model.agent import agent_run_snapshot as agent_run_snapshot
from trace_harness.model.agent import validate_agent_run_ir as validate_agent_run_ir
from trace_harness.model.analysis import analysis_snapshot as analysis_snapshot
from trace_harness.model.context import TraceContext as TraceContext
from trace_harness.model.ir import TraceView as TraceView
from trace_harness.model.ir import dump_view as dump_view
from trace_harness.model.ir import load_view as load_view
from trace_harness.model.node import Finding as Finding
from trace_harness.model.node import Node as Node
from trace_harness.model.span import NormSpan as NormSpan
from trace_harness.model.spec import KindSpec as KindSpec
from trace_harness.model.spec import SpecSet as SpecSet
from trace_harness.model.spec import merge as merge
from trace_harness.model.viewtree import NodeTreeExtractor as NodeTreeExtractor
from trace_harness.view.explore import render_explore as render_explore
from trace_harness.view.facet import DefaultFacet as DefaultFacet
from trace_harness.view.facet import Facet as Facet
from trace_harness.view.facet import PerspectiveLevel as PerspectiveLevel
from trace_harness.view.facet import RenderConfig as RenderConfig
from trace_harness.view.facet import TracePerspective as TracePerspective
from trace_harness.view.text import render_text as render_text
