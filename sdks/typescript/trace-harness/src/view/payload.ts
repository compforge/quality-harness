import type { TraceContext } from "../model/context";
import type { Node } from "../model/node";
import type { Measurements } from "../model/measurement";
import { measurementRows } from "./measurements";
import { DisplayName, nameProjections, type DisplayNode } from "./display";
import { toolNameDetail } from "./tool-name";
const JSON_DECODE_LIMIT = 4;
function structuredJson(value: unknown): unknown | undefined {
  let candidate = value;
  for (let depth = 0; depth < JSON_DECODE_LIMIT && typeof candidate === "string"; depth += 1) {
    const text = candidate.trim();
    if (!text) return undefined;
    try {
      candidate = JSON.parse(text) as unknown;
    } catch {
      return undefined;
    }
  }
  return candidate !== null && typeof candidate === "object" ? candidate : undefined;
}

function attrPayload(value: unknown): Record<string, unknown> {
  const json = structuredJson(value);
  if (json !== undefined) return { kind: "json", value: json };
  let text = typeof value === "string" ? value : (JSON.stringify(value) ?? String(value));
  return { kind: "text", value: text };
}

export function displayPayload(
  context: TraceContext,
  display: DisplayNode,
  byId: Map<string, Node>,
  measurements: Measurements,
  path: string,
): Record<string, unknown> {
  const children = display.children.map((child, i) => (
    displayPayload(context, child, byId, measurements, `${path}.${i}`)
  ));
  const findings = display.findings.map((finding) => ({
    severity: finding.severity,
    source: finding.source,
    note: finding.note ?? "",
  }));
  const node = display.kind && display.node_ids.length ? byId.get(display.node_ids[0]!) : undefined;
  if (node) {
    const displayName = node.kind === "tool-call"
      ? String(node.facts.tool ?? display.name)
      : display.name;
    const argumentsValue = node.kind === "tool-call"
      ? context.raw_attr(String(node.facts.io_span ?? node.primary_span_id))["gen_ai.tool.call.arguments"]
      : undefined;
    const compact = typeof display.compact === "function"
      ? display
      : node.kind === "tool-call"
      ? new DisplayName(displayName, toolNameDetail(argumentsValue))
      : display;
    return {
      node_id: node.node_id,
      source_node_ids: [...display.node_ids],
      kind: display.kind,
      name: display.name,
      name_variants: nameProjections(compact, display.name),
      service: node.service ?? "",
      start_ms: node.start_ms,
      duration_ms: Number(node.facts.wall_ms ?? node.duration_ms),
      has_error: node.has_error,
      error: node.has_error ? node.error_text : "",
      brief: display.brief.map((field) => `${field.label}=${field.value}`).join("  "),
      findings,
      facts: Object.fromEntries(
        Object.entries(node.facts)
          .filter(([key]) => !key.startsWith("_"))
          .map(([key, value]) => [key, value]),
      ),
      measurements: measurementRows(measurements, node.node_id),
      span_ids: node.span_ids,
      primary_span_id: node.primary_span_id,
      error_span_ids: node.error_span_ids,
      folded: display.folded,
      children,
    };
  }
  const nodes = display.node_ids.map((id) => byId.get(id)).filter((item): item is Node => Boolean(item));
  const start = nodes.length ? Math.min(...nodes.map((item) => item.start_ms)) : 0;
  const end = nodes.length ? Math.max(...nodes.map((item) => item.end_ms)) : 0;
  return {
    // Nested service/API groups can reference identical members; use their unique display path.
    node_id: `fold:${path}`,
    source_node_ids: [...display.node_ids],
    kind: "",
    name: display.name,
    name_variants: nameProjections(display),
    service: "",
    start_ms: start,
    duration_ms: end - start,
    has_error: false,
    error: "",
    brief: display.brief.map((field) => `${field.label}=${field.value}`).join("  "),
    findings,
    facts: {},
    details: {},
    span_ids: [],
    primary_span_id: "",
    error_span_ids: [],
    folded: display.folded,
    children,
  };
}

export function spanPayload(context: TraceContext, spanId: string): Record<string, unknown> {
  const span = context.spans.get(spanId)!;
  const attrs = Object.fromEntries(
    Object.entries(span.attrs).map(([key, value]) => [key, attrPayload(value)]),
  );
  return {
    service: span.service ?? "",
    operation: span.name,
    duration_ms: span.dur_ms,
    has_error: span.has_error,
    error: span.has_error ? context.error_text(spanId) : "",
    attrs,
  };
}

