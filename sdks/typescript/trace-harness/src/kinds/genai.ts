import type { Finding, Node } from "../model/node";
import { SpecSet, type KindSpec } from "../model/spec";
import type { NormSpan } from "../model/span";
import { durationMetric, formatBytes, formatMs } from "./base";

const CHAT_OPS = new Set(["chat", "text_completion", "generate_content", "completion"]);
const TOOL_OPS = new Set(["execute_tool"]);
const AGENT_OPS = new Set(["invoke_agent", "create_agent"]);
const LLM_URL_MARKS = ["/chat/completions", "/embeddings", "/rerank"];

function operation(span: NormSpan): string {
  return String(span.attr("gen_ai.operation.name") ?? "");
}

function modelSpec(): KindSpec {
  return {
    kind: "model-call",
    matches: (span) => {
      const op = operation(span);
      if (CHAT_OPS.has(op)) return true;
      const hasModel = span.attr("gen_ai.request.model", "llm.model_name") !== undefined;
      return hasModel && !TOOL_OPS.has(op) && !AGENT_OPS.has(op);
    },
    build: (primary) => {
      const facts: Record<string, unknown> = {};
      const model = primary.attr(
        "gen_ai.response.model",
        "gen_ai.request.model",
        "llm.response.model",
        "llm.model_name",
      );
      if (model !== undefined) facts.model = model;
      const input = primary.num(
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.prompt_tokens",
        "llm.token_count.prompt",
      );
      const output = primary.num(
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.completion_tokens",
        "llm.token_count.completion",
      );
      const total = primary.num("gen_ai.usage.total_tokens", "llm.token_count.total");
      if (input !== undefined) facts.in_tokens = input;
      if (output !== undefined) facts.out_tokens = output;
      if (total !== undefined) facts.total_tokens = total;
      if (primary.attr("gen_ai.prompt", "gen_ai.input.messages") !== undefined) {
        facts.io_span = primary.span_id;
      }
      return facts;
    },
    metrics: {
      ...durationMetric(),
      in_tokens: (node) => Number(node.facts.in_tokens),
      out_tokens: (node) => Number(node.facts.out_tokens),
    },
    rules: [(node): Finding[] => {
      if (node.has_error || node.facts.out_tokens !== 0) return [];
      return [{ ref: node.node_id, source: "empty_output", severity: "warn", note: "模型返回 0 output tokens（疑似空响应）" }];
    }],
    project: (node) => {
      const fields = [];
      if (node.facts.model) fields.push({ label: "model", value: String(node.facts.model), emphasis: "strong" as const });
      const tokens = [];
      if (node.facts.in_tokens !== undefined) tokens.push(`in ${Math.trunc(Number(node.facts.in_tokens))}`);
      if (node.facts.out_tokens !== undefined) tokens.push(`out ${Math.trunc(Number(node.facts.out_tokens))}`);
      if (tokens.length) fields.push({ label: "tok", value: tokens.join(" / ") });
      fields.push({ label: "dur", value: formatMs(node.facts.duration_ms), emphasis: "dim" as const });
      if (node.facts.http_status !== undefined) fields.push({ label: "http", value: String(node.facts.http_status) });
      return fields;
    },
  };
}

function toolSpec(): KindSpec {
  return {
    kind: "tool-call",
    matches: (span) => TOOL_OPS.has(operation(span)) || span.attr("gen_ai.tool.name") !== undefined,
    build: (primary) => {
      const facts: Record<string, unknown> = {};
      const tool = primary.attr("gen_ai.tool.name");
      const result = primary.attr("gen_ai.tool.call.result");
      if (tool !== undefined) facts.tool = tool;
      if (result !== undefined) facts.result_bytes = String(result).length;
      if (primary.attr("gen_ai.tool.call.arguments", "gen_ai.tool.call.result") !== undefined) {
        facts.io_span = primary.span_id;
      }
      return facts;
    },
    metrics: { ...durationMetric(), result_bytes: (node) => Number(node.facts.result_bytes) },
    project: (node) => [
      ...(node.facts.tool ? [{ label: "tool", value: String(node.facts.tool), emphasis: "strong" as const }] : []),
      { label: "dur", value: formatMs(node.facts.duration_ms), emphasis: "dim" as const },
      ...(node.facts.result_bytes !== undefined
        ? [{ label: "result", value: formatBytes(node.facts.result_bytes), emphasis: "dim" as const }]
        : []),
    ],
  };
}

function agentSpec(): KindSpec {
  return {
    kind: "agent",
    matches: (span) => AGENT_OPS.has(operation(span)) || span.attr("gen_ai.agent.name") !== undefined,
    build: (primary) => {
      const agent = primary.attr("gen_ai.agent.name");
      return agent === undefined ? {} : { agent };
    },
    metrics: durationMetric(),
    project: (node) => [
      ...(node.facts.agent ? [{ label: "agent", value: String(node.facts.agent), emphasis: "strong" as const }] : []),
      { label: "dur", value: formatMs(node.facts.duration_ms), emphasis: "dim" as const },
    ],
  };
}

function httpSpec(): KindSpec {
  return {
    kind: "http",
    matches: (span) => {
      const isHttp = span.attr("http.request.method", "http.method", "url.full", "http.url") !== undefined;
      const url = String(span.attr("url.full", "http.url") ?? "");
      return isHttp && LLM_URL_MARKS.some((mark) => url.includes(mark));
    },
    build: (primary) => {
      const facts: Record<string, unknown> = {};
      const status = primary.num("http.response.status_code", "http.status_code");
      const method = primary.attr("http.request.method", "http.method");
      const url = primary.attr("url.full", "http.url");
      if (status !== undefined) facts.status = Math.trunc(status);
      if (method !== undefined) facts.method = method;
      if (url !== undefined) facts.url = url;
      return facts;
    },
    metrics: durationMetric(),
    project: (node: Node) => [
      ...(node.facts.method ? [{ label: "method", value: String(node.facts.method), emphasis: "dim" as const }] : []),
      ...(node.facts.status !== undefined
        ? [{
            label: "http",
            value: String(node.facts.status),
            emphasis: Number(node.facts.status) === 200 ? "normal" as const : "strong" as const,
          }]
        : []),
      { label: "dur", value: formatMs(node.facts.duration_ms), emphasis: "dim" as const },
    ],
  };
}

export function genAiSpecs(): SpecSet {
  return new SpecSet([toolSpec(), agentSpec(), modelSpec(), httpSpec()]);
}
