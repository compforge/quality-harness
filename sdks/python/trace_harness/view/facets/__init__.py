"""harness 自带通用 facet。零 biz，每个 TraceHarness 独立持有。

DefaultFacet 是注册表内置兜底（在 registry 里直接持有，不在此登记）。这里登记需要
"按 kind/特征抢在默认之前"的通用 facet。biz facet（按 agent_type 等）由 skill 注册。
"""

from trace_harness.view.facet import Facet
from trace_harness.view.facets.agent import AgentFacet, ToolCallFacet
from trace_harness.view.facets.model_call import ModelCallFacet
from trace_harness.view.facets.service import ServiceFacet


def builtin_facets() -> tuple[Facet, ...]:
    """返回一组新的通用 facet，供每个 scoped TraceHarness 独立持有。"""
    return ServiceFacet(), ModelCallFacet(), AgentFacet(), ToolCallFacet()
