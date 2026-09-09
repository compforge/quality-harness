"""行为模式判读（behavior smells，设计文档 §3.7）——挖 agent 设计的系统性弱点。

与排障判据（错误/超时/离群）的关键差异：**单条 trace 里 pattern 只是嫌疑**（连调 3 次可能
只是网络抖动），**跨 trace 聚合后才成结论**（"该 tool 在 23% 的 trace 里被连续调 ≥3 次
→ desc 或出参可用性有问题"）。故 source 统一带 `pattern:` 前缀，corpus 的 pattern_rates
算子按它聚合出现率；severity 恒 warn、note 写明"单条是嫌疑"。

v0 只有 tool_churn；后续模式（ping_pong / 盲重试 / dead_tool…）按 §3.7 目录逐个沉淀——
通用模式（只依赖 kind/name/时序）进这里，域专属随域 KindSpec 包。
"""

from __future__ import annotations

from trace_harness.model.context import TraceContext
from trace_harness.model.node import Finding, Node

# tool_churn 作用的 kind。"tool-call" 是跨 spec 包的公共词汇（genai 与各域包同名）——
# 循环 agent 连续多轮 model-call 是正常形态，churn 语义只对工具调用成立。
_CHURN_KIND = "tool-call"
_CHURN_MIN_RUN = 3  # 同一父节点下同一 tool 连续 ≥N 次 → 嫌疑


def _tool_identity(n: Node) -> str:
    return str(n.facts.get("tool") or n.name)


def find_patterns(ctx: TraceContext) -> list[Finding]:
    return _tool_churn(ctx)


def _tool_churn(ctx: TraceContext) -> list[Finding]:
    """同一父节点下、同一 tool **背靠背**连续调用 ≥N 次（中间隔任何其它节点即断开——
    think→call→think→call 是正常 agent 循环，背靠背重复才指向 desc/出参问题）。"""
    out: list[Finding] = []
    view = ctx.view()

    def flush(run: list[Node]) -> None:
        if len(run) < _CHURN_MIN_RUN:
            return
        name = _tool_identity(run[0])
        out.append(
            Finding(
                run[-1].node_id,
                "pattern:tool_churn",
                "warn",
                note=(
                    f"tool {name} 连续调用 {len(run)} 次（首调 span={run[0].primary_span_id}）"
                    "——疑似 desc 不清或出参格式模型用不了；单条是嫌疑，跨 trace 出现率见 corpus"
                ),
            )
        )

    for parent in ctx.nodes:
        kids = view.children(parent)
        if len(kids) < _CHURN_MIN_RUN:
            continue
        run: list[Node] = []
        for k in kids:
            if k.kind == _CHURN_KIND and run and _tool_identity(run[-1]) == _tool_identity(k):
                run.append(k)
            else:
                flush(run)
                run = [k] if k.kind == _CHURN_KIND else []
        flush(run)
    return out
