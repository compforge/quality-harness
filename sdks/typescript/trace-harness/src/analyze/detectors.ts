import type { TraceContext } from "../model/context";
import type { Node } from "../model/node";
import type { Detector } from "./registry";

const HOLE_MIN_GAP_MS = 1000;
const HOLE_MIN_FRAC = 0.2;

function intervalUnion(intervals: Array<[number, number]>): number {
  if (intervals.length === 0) return 0;
  const sorted = [...intervals].sort((left, right) => left[0] - right[0]);
  let [start, end] = sorted[0]!;
  let total = 0;
  for (const [nextStart, nextEnd] of sorted.slice(1)) {
    if (nextStart <= end) end = Math.max(end, nextEnd);
    else {
      total += end - start;
      [start, end] = [nextStart, nextEnd];
    }
  }
  return total + end - start;
}

function errorSignature(context: TraceContext, node: Node): string {
  const span = context.spans.get(node.error_anchor);
  const type = span?.error_events[0]?.type;
  return type || context.error_text(node.error_anchor).slice(0, 60);
}

const detached: Detector = (node, context) => {
  const parentSpanId = context.spans.get(node.primary_span_id)?.parent_span_id;
  if (!parentSpanId || context.spans.has(parentSpanId)) return [];
  return [{
    ref: node.node_id,
    source: "detached",
    severity: "warn",
    note: `父 span ${parentSpanId.slice(0, 8)}… 不在本 trace（跨服务断链 / 采样丢失）`,
  }];
};

const observationHole: Detector = (node, context) => {
  const spec = context.specs.get(node.kind);
  if (spec?.obs_hole === false) return [];
  const children = context.view().children(node);
  if (children.length === 0 || node.duration_ms <= 0) return [];
  const intervals = children
    .map((child): [number, number] => [
      Math.max(node.start_ms, child.start_ms),
      Math.min(node.end_ms, child.end_ms),
    ])
    .filter(([start, end]) => end > start);
  const gap = node.duration_ms - intervalUnion(intervals);
  if (gap < HOLE_MIN_GAP_MS || gap < HOLE_MIN_FRAC * node.duration_ms) return [];
  return [{
    ref: node.node_id,
    source: "obs_hole",
    severity: "info",
    note: `${gap.toFixed(0)}ms 未被子节点覆盖（疑似未埋点的耗时）`,
  }];
};

const propagated: Detector = (node, context) => {
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

const DETECTORS = [detached, observationHole, propagated] satisfies Detector[];

export function builtinDetectors(): Detector[] {
  return [...DETECTORS];
}
