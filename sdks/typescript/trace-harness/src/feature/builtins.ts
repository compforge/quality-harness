import type { Node } from "../model/node";
import type { FeatureContext } from "./context";
import type { Feature } from "./feature";

function intervalUnion(intervals: Array<[number, number]>): number {
  if (intervals.length === 0) return 0;
  const sorted = [...intervals].sort((a, b) => a[0] - b[0]);
  let [start, end] = sorted[0]!;
  let total = 0;
  for (const [nextStart, nextEnd] of sorted.slice(1)) {
    if (nextStart <= end) {
      end = Math.max(end, nextEnd);
    } else {
      total += end - start;
      [start, end] = [nextStart, nextEnd];
    }
  }
  return total + end - start;
}

function selfMs(node: Node, context: FeatureContext): Record<string, unknown> {
  const children = context.children(node);
  if (children.length === 0) return {};
  const covered = intervalUnion(children.map((child) => [child.start_ms, child.end_ms]));
  return { self_ms: Math.round(Math.max(0, node.duration_ms - covered) * 1000) / 1000 };
}

function httpStatus(node: Node, context: FeatureContext): Record<string, unknown> {
  const statuses = context.children(node)
    .filter((child) => child.kind === "http")
    .map((child) => context.get(child, "status"))
    .filter((status): status is number => status !== undefined && status !== null)
    .map(Number);
  if (statuses.length === 0) return {};
  return { http_status: statuses.find((status) => status !== 200) ?? statuses[0] };
}

const FEATURES = [{ produces: ["self_ms"], applies: () => true, compute: selfMs, bake: true }, {
  produces: ["http_status"],
  applies: (node) => node.kind === "model-call",
  compute: httpStatus,
  bake: true,
}] satisfies Feature[];

export function builtinFeatures(): Feature[] {
  return [...FEATURES];
}
