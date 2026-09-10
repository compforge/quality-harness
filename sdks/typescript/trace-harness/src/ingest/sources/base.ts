import type { EvidenceRef } from "../../loading/model";
import type { NormSpan } from "../../model/span";

export interface SpanQuery {
  trace_ids?: readonly string[];
  attr_eq?: Readonly<Record<string, string>>;
  error_only?: boolean;
  service?: string;
  since_ms?: number;
  until_ms?: number;
  limit?: number;
  order?: "trace_id" | "latest";
  operation_names?: readonly string[];
}
/** Source owns backend projection and completeness checks, never semantic node classification.
 * select returns unique trace IDs; fetch returns every span with projected attributes.
 * read(null) returns full evidence. Implementations must honor cancellation and bound I/O time.
 * The session owns this Source and calls close exactly once after its operations settle.
 */
export interface Source {
  readonly namespace: string;
  select(query: SpanQuery, signal?: AbortSignal): AsyncIterable<string>;
  fetch(traceId: string, fields: readonly string[], signal?: AbortSignal): Promise<Map<string, NormSpan>>;
  read(refs: readonly EvidenceRef[], fields: readonly string[] | null, signal?: AbortSignal): Promise<Map<string, NormSpan>>;
  close(): Promise<void>;
}
