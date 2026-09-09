import type { Node } from "./node";
import type { KindSpec } from "./spec";
import { spanErrorText, type NormSpan } from "./span";
import { buildView, type ViewTree } from "./viewtree";

export class TraceContext {
  #view?: ViewTree;

  constructor(
    readonly trace_id: string,
    readonly spans: Map<string, NormSpan>,
    readonly nodes: Node[],
    readonly specs: Map<string, KindSpec>,
  ) {}

  get span_count(): number {
    return this.spans.size;
  }

  view(): ViewTree {
    return this.#view ??= buildView(this.nodes);
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
