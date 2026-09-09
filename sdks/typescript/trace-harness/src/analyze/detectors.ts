import { httpRequestPatterns } from "./http";
import type { TraceContext } from "../model/context";
import type { Node } from "../model/node";
import type { Detector } from "./registry";

const HOLE_MIN_GAP_MS = 1000;
const HOLE_MIN_FRAC = 0.2;


function errorSignature(context: TraceContext, node: Node): string {
  const span = context.spans.get(node.error_anchor);
  const type = span?.error_events[0]?.type;
  return type || context.error_text(node.error_anchor).slice(0, 60);
}

const detached: Detector = (node, analysis) => {
  const context = analysis.trace;
  const parentSpanId = context.spans.get(node.primary_span_id)?.parent_span_id;
  if (!parentSpanId || context.spans.has(parentSpanId)) return [];
  return [{
    ref: node.node_id,
    source: "detached",
    severity: "warn",
    note: `父 span ${parentSpanId.slice(0, 8)}… 不在本 trace（跨服务断链 / 采样丢失）`,
  }];
};

const observationHole: Detector = (node, analysis) => {
  const context = analysis.trace;
  const spec = context.specs.get(node.kind);
  if (spec?.obs_hole === false) return [];
  const children = context.view().children(node);
  if (children.length === 0 || node.duration_ms <= 0) return [];
  const measurement = analysis.measurements.get(node.node_id, "self_ms");
  if (measurement?.status !== "measured") return [];
  const gap = Number(measurement.values.self_ms);
  if (gap < HOLE_MIN_GAP_MS || gap < HOLE_MIN_FRAC * node.duration_ms) return [];
  return [{
    ref: node.node_id,
    source: "obs_hole",
    severity: "info",
    note: `${gap.toFixed(0)}ms 未被子节点覆盖（疑似未埋点的耗时）`,
  }];
};

const propagated: Detector = (node, analysis) => {
  const context = analysis.trace;
  if (!node.has_error) return [];
  const signature = errorSignature(context, node);
  const stack = [...context.view().children(node)];
  while (stack.length) {
    const descendant = stack.pop()!;
    if (descendant.has_error && errorSignature(context, descendant) === signature) {
      return [{
        ref: node.node_id,
        source: "propagated",
        severity: "info",
        note: `错误传播副本（源头在更深 node；sig=${signature}）`,
      }];
    }
    stack.push(...context.view().children(descendant));
  }
  return [];
};

const DETECTORS = [detached, observationHole, propagated, httpRequestPatterns] satisfies Detector[];

export function builtinDetectors(): Detector[] {
  return [...DETECTORS];
}
