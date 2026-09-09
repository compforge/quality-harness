"""OTel GenAI 语义约定的通用 kind：model-call / tool-call / agent。

只认 OTel `gen_ai.*` / `http.*` 标准字段，**零域业务知识**——AS 专属（aigw 网关锚点、
sandbox 启动归因）随域包留消费方，组合时 merge。每个 spec 的 build 是业务字段隔离闸：
gen_ai.* 在此抽成命名 facts 列，下游只见 model/in_tokens/http_status 这些列名。
"""

from __future__ import annotations

from trace_harness.kinds.base import _fmt_bytes, _fmt_ms, duration_metric
from trace_harness.model.node import Field, Finding, Node
from trace_harness.model.span import NormSpan
from trace_harness.model.spec import KindSpec, SpecSet

# OTel GenAI operation.name 取值
_CHAT_OPS = {"chat", "text_completion", "generate_content", "completion"}
_TOOL_OPS = {"execute_tool"}
_AGENT_OPS = {"invoke_agent", "create_agent"}
# 模型端点 URL 标记：只有打到这些端点的 http-client span 才是"模型调用的那次传输"，才成 http kind。
# 其余 http（kb / control / 内部 RPC…）不成 node、照旧进残余 service 组——避免大 trace 上 http 爆炸。
_LLM_URL_MARKS = ("/chat/completions", "/embeddings", "/rerank")


def _op(s: NormSpan) -> str:
    return str(s.attr("gen_ai.operation.name") or "")


def _is_http(s: NormSpan) -> bool:
    return s.attr("http.request.method", "http.method", "url.full", "http.url") is not None


def _is_llm_http(s: NormSpan) -> bool:
    if not _is_http(s):
        return False
    url = str(s.attr("url.full", "http.url") or "")
    return any(m in url for m in _LLM_URL_MARKS)


# —— model-call ——


def _match_model(s: NormSpan) -> bool:
    op = _op(s)
    if op in _CHAT_OPS:
        return True
    # 有 model 名且不是工具/agent 调用 → 视为模型调用。别名只收两套标准约定：
    # OTel GenAI（gen_ai.*）与 OpenInference（llm.*）；厂商私货（Vercel ai.*、genkit:*）
    # 留各自域 spec，不让通用 spec 腐烂成 Langfuse 式单体兼容层。
    has_model = s.attr("gen_ai.request.model", "llm.model_name") is not None
    return has_model and op not in (_TOOL_OPS | _AGENT_OPS)


def _build_model(primary: NormSpan, satellites: list[NormSpan]) -> dict:
    facts: dict = {}
    # fallback 链只跨 OTel GenAI + OpenInference 两套标准约定（response 优先于 request）。
    model = primary.attr(
        "gen_ai.response.model", "gen_ai.request.model", "llm.response.model", "llm.model_name"
    )
    if model:
        facts["model"] = model
    in_tok = primary.num(
        "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "llm.token_count.prompt"
    )
    if in_tok is not None:
        facts["in_tokens"] = in_tok
    out_tok = primary.num(
        "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens", "llm.token_count.completion"
    )
    if out_tok is not None:
        facts["out_tokens"] = out_tok
    total_tok = primary.num("gen_ai.usage.total_tokens", "llm.token_count.total")
    if total_tok is not None:
        facts["total_tokens"] = total_tok
    # http_status 不在此抽：1:1 后 http 是独立子 node，由 derive 的 HttpStatusOp 从 http 子卷上来。
    # 原文填指针：prompt/completion 在 primary 上，按需经 raw_attr 回原文（probe 阶段）
    if primary.attr("gen_ai.prompt", "gen_ai.input.messages") is not None:
        facts["io_span"] = primary.span_id
    return facts


def _project_model(n: Node) -> list[Field]:
    brief = []
    if n.facts.get("model"):
        brief.append(Field(label="model", value=str(n.facts["model"]), emphasis="strong"))
    toks = []
    if n.facts.get("in_tokens") is not None:
        toks.append(f"in {int(n.facts['in_tokens'])}")
    if n.facts.get("out_tokens") is not None:
        toks.append(f"out {int(n.facts['out_tokens'])}")
    if toks:
        brief.append(Field(label="tok", value=" / ".join(toks)))
    brief.append(Field(label="dur", value=_fmt_ms(n.facts.get("duration_ms")), emphasis="dim"))
    if n.facts.get("http_status"):
        brief.append(Field(label="http", value=str(n.facts["http_status"])))
    return brief


def _rule_empty_output(n: Node, ctx) -> list[Finding]:
    """通用 LLM 语义判读：成功调用却返回 0 output tokens —— 疑似空响应。

    域专属 rule（aigw 网关超时锚点、特定错误码归因）留各自域 spec，通用 spec 只放
    与厂商无关的语义判读。
    """
    if n.has_error or n.facts.get("out_tokens") != 0:
        return []
    return [
        Finding(n.node_id, "empty_output", "warn", note="模型返回 0 output tokens（疑似空响应）")
    ]


def _model_spec() -> KindSpec:
    return KindSpec(
        kind="model-call",
        matches=_match_model,
        build=_build_model,
        metrics={
            **duration_metric(),
            "in_tokens": lambda n: n.facts.get("in_tokens"),
            "out_tokens": lambda n: n.facts.get("out_tokens"),
        },
        rules=(_rule_empty_output,),
        project=_project_model,
    )


# —— tool-call ——


def _match_tool(s: NormSpan) -> bool:
    return _op(s) in _TOOL_OPS or s.attr("gen_ai.tool.name") is not None


def _build_tool(primary: NormSpan, satellites: list[NormSpan]) -> dict:
    facts: dict = {}
    name = primary.attr("gen_ai.tool.name")
    if name:
        facts["tool"] = name
    # result size is the tool analog of a model's out_tokens: a cheap, content-free
    # signal (empty / oversized results are diagnosable) from the OTel gen_ai
    # tool-result attr.
    result = primary.attr("gen_ai.tool.call.result")
    if result is not None:
        facts["result_bytes"] = len(str(result))
    # io pointer, mirroring model-call's io_span: a tool call's arguments + result
    # live on this span; dump-io and the diagnose evidence probe fetch the raw text
    # through it. Without it a tool node exposed only its name — nothing about WHAT
    # it ran or WHAT came back.
    if primary.attr("gen_ai.tool.call.arguments", "gen_ai.tool.call.result") is not None:
        facts["io_span"] = primary.span_id
    return facts


def _project_tool(n: Node) -> list[Field]:
    brief = []
    if n.facts.get("tool"):
        brief.append(Field(label="tool", value=str(n.facts["tool"]), emphasis="strong"))
    brief.append(Field(label="dur", value=_fmt_ms(n.facts.get("duration_ms")), emphasis="dim"))
    if n.facts.get("result_bytes") is not None:
        brief.append(
            Field(label="result", value=_fmt_bytes(n.facts["result_bytes"]), emphasis="dim")
        )
    return brief


def _tool_spec() -> KindSpec:
    return KindSpec(
        kind="tool-call",
        matches=_match_tool,
        build=_build_tool,
        metrics={**duration_metric(), "result_bytes": lambda n: n.facts.get("result_bytes")},
        project=_project_tool,
    )


# —— agent ——


def _match_agent(s: NormSpan) -> bool:
    return _op(s) in _AGENT_OPS or s.attr("gen_ai.agent.name") is not None


def _build_agent(primary: NormSpan, satellites: list[NormSpan]) -> dict:
    facts: dict = {}
    name = primary.attr("gen_ai.agent.name")
    if name:
        facts["agent"] = name
    return facts


def _project_agent(n: Node) -> list[Field]:
    brief = []
    if n.facts.get("agent"):
        brief.append(Field(label="agent", value=str(n.facts["agent"]), emphasis="strong"))
    brief.append(Field(label="dur", value=_fmt_ms(n.facts.get("duration_ms")), emphasis="dim"))
    return brief


def _agent_spec() -> KindSpec:
    return KindSpec(
        kind="agent",
        matches=_match_agent,
        build=_build_agent,
        metrics=duration_metric(),
        project=_project_agent,
    )


# —— http（1:1：真正打端点的 HTTP client span 自成 node，由父 model-call/action 经父子边引用、
#    facet 默认折；其自身 duration/status 是延迟拆解的关键，不再被 fusion 化掉）——


def _match_http(s: NormSpan) -> bool:
    return _is_llm_http(s)


def _build_http(primary: NormSpan, satellites: list[NormSpan]) -> dict:
    facts: dict = {}
    st = primary.num("http.response.status_code", "http.status_code")
    if st is not None:
        facts["status"] = int(st)
    method = primary.attr("http.request.method", "http.method")
    if method:
        facts["method"] = method
    url = primary.attr("url.full", "http.url")
    if url:
        facts["url"] = url
    return facts


def _project_http(n: Node) -> list[Field]:
    brief = []
    if n.facts.get("method"):
        brief.append(Field(label="method", value=str(n.facts["method"]), emphasis="dim"))
    if n.facts.get("status") is not None:
        st = str(n.facts["status"])
        brief.append(Field(label="http", value=st, emphasis="strong" if st != "200" else "normal"))
    brief.append(Field(label="dur", value=_fmt_ms(n.facts.get("duration_ms")), emphasis="dim"))
    return brief


def _http_spec() -> KindSpec:
    return KindSpec(
        kind="http",
        matches=_match_http,
        build=_build_http,
        metrics=duration_metric(),
        project=_project_http,
    )


def specs() -> SpecSet:
    """通用 GenAI SpecSet。classify 顺序 = 识别优先级：tool/agent 窄判在前，model 兜 chat，
    http 垫底（只有不属 model/tool/agent 的 http-client span 才落 http kind）。"""
    return SpecSet([_tool_spec(), _agent_spec(), _model_spec(), _http_spec()])
