import type { TransformContext } from "../transform";
import type { Node } from "./node";
import type { KindSpec } from "./spec";
import { spanErrorText, type NormSpan } from "./span";
import { buildNodeTree, type NodeTree } from "./node-tree";

export class TraceContext {
  #tree?: NodeTree;
  transforms?: TransformContext;

  constructor(
    readonly trace_id: string,
    readonly spans: Map<string, NormSpan>,
    readonly nodes: Node[],
    readonly specs: Map<string, KindSpec>,
    readonly observed_span_count?: number,
  ) {}

  get span_count(): number {
    return this.observed_span_count ?? this.spans.size;
  }

  tree(): NodeTree {
    return this.#tree ??= buildNodeTree(this.nodes);
  }

  raw(spanId: string): Record<string, unknown> {
    return this.spans.get(spanId)?.raw ?? {};
  }

  raw_attr(spanId: string): Record<string, unknown> {
    return this.spans.get(spanId)?.attrs ?? {};
  }

  error_text(spanId: string): string {
    return spanErrorText(this.spans.get(spanId));
  }
}
