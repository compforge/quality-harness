export type SpanAttributes = Record<string, unknown>;

export interface SpanEvent {
  name: string;
  timestamp_ms?: number;
  attrs: SpanAttributes;
}

export interface ErrorEvent {
  type: string;
  message: string;
  stacktrace: string;
}

export class NormSpan {
  constructor(
    readonly span_id: string,
    readonly parent_span_id: string | undefined,
    readonly name: string,
    readonly start_ms: number,
    readonly dur_ms: number,
    readonly service: string | undefined,
    readonly has_error: boolean,
    readonly attrs: SpanAttributes,
    readonly raw: Record<string, unknown>,
    readonly error_events: ErrorEvent[] = [],
    readonly events: SpanEvent[] = [],
  ) {}

  get end_ms(): number {
    return this.start_ms + this.dur_ms;
  }

  attr(...names: string[]): unknown {
    for (const name of names) {
      const value = this.attrs[name];
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return undefined;
  }

  num(...names: string[]): number | undefined {
    const value = this.attr(...names);
    if (value === undefined || value === null || value === "") return undefined;
    const number = Number(value);
    return Number.isFinite(number) ? number : undefined;
  }
}

export function spanErrorText(span: NormSpan | undefined): string {
  if (!span) return "";
  const first = span.error_events[0];
  if (first) return `${first.type}: ${first.message}`.replace(/^: |: $/g, "");
  return String(span.attrs["otel.status_description"] ?? "");
}
