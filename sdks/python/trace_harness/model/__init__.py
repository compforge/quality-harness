"""model —— 分析枢纽：所有消费者（analyze/view/corpus）围绕它扇出。零域知识。

数据本体 NormSpan / Node（+ Field/Finding）、语义契约 spec(KindSpec/SpecSet)、
结构索引 viewtree、可序列化形态 ir(TraceView + nodes.json dump/load)。ingest 产出它，
其余只读它。
"""

from __future__ import annotations

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
from trace_harness.model.context import TraceContext as TraceContext
from trace_harness.model.ir import TraceView as TraceView
from trace_harness.model.ir import dump_view as dump_view
from trace_harness.model.ir import load_view as load_view
from trace_harness.model.node import Field as Field
from trace_harness.model.node import Finding as Finding
from trace_harness.model.node import Node as Node
from trace_harness.model.span import NormSpan as NormSpan
from trace_harness.model.spec import KindSpec as KindSpec
from trace_harness.model.spec import SpecSet as SpecSet
from trace_harness.model.viewtree import NodeTreeExtractor as NodeTreeExtractor
from trace_harness.model.viewtree import ViewTree as ViewTree
from trace_harness.model.viewtree import build_view as build_view
from trace_harness.model.viewtree import iter_nodes as iter_nodes
