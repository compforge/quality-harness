import type { Field, Node } from "../model/node";
import type { KindSpec } from "../model/spec";

export const SERVICE_KIND = "service";

export function formatMs(value: unknown): string {
  const ms = Number(value);
  if (!Number.isFinite(ms)) return "?";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(0)}ms`;
}

export function formatBytes(value: unknown): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "?";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${bytes}B`;
}

export function durationMetric(): Record<string, (node: Node) => number | undefined> {
  return { duration_ms: (node) => Number(node.facts.duration_ms) };
}

export function serviceSpec(): KindSpec {
  return {
    kind: SERVICE_KIND,
    matches: () => false,
    project: (node): Field[] => [
      { label: "×", value: String(node.facts.count ?? "?"), emphasis: "dim" },
      { label: "sum", value: formatMs(node.facts.sum_ms), emphasis: "dim" },
    ],
  };
}
