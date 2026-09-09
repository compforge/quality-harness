import { NormSpan, type ErrorEvent, type SpanAttributes, type SpanEvent } from "../model/span";

type RecordValue = Record<string, any>;

function flattenTags(document: RecordValue): SpanAttributes {
  const tags = Array.isArray(document.tags) ? document.tags : [];
  return Object.fromEntries(tags.filter((tag) => tag?.key).map((tag) => [tag.key, tag.value]));
}

function parentOf(document: RecordValue): string | undefined {
  const references = Array.isArray(document.references) ? document.references : [];
  return references.find((reference) =>
    (reference?.refType ?? "CHILD_OF") === "CHILD_OF" && reference?.spanID,
  )?.spanID;
}

export function normalizeJaegerSpan(document: RecordValue): NormSpan | undefined {
  const spanId = document.spanID;
  if (!spanId) return undefined;
  const attrs = flattenTags(document);
  let hasError = String(attrs["otel.status_code"] ?? "").toUpperCase() === "ERROR";
  if ([true, "true", "True"].includes(attrs.error as any)) hasError = true;
  const events: SpanEvent[] = [];
  const errorEvents: ErrorEvent[] = [];
  for (const log of Array.isArray(document.logs) ? document.logs : []) {
    const fields = Object.fromEntries(
      (Array.isArray(log?.fields) ? log.fields : []).map((field: RecordValue) => [field.key, field.value]),
    );
    const name = String(fields.event ?? "");
    events.push({
      name,
      timestamp_ms: log.timestamp === undefined ? undefined : Number(log.timestamp) / 1000,
      attrs: Object.fromEntries(Object.entries(fields).filter(([key]) => key !== "event")),
    });
    if (name === "exception" || fields["exception.type"]) {
      errorEvents.push({
        type: String(fields["exception.type"] ?? ""),
        message: String(fields["exception.message"] ?? ""),
        stacktrace: String(fields["exception.stacktrace"] ?? ""),
      });
      hasError = true;
    }
  }
  return new NormSpan(
    String(spanId),
    parentOf(document),
    String(document.operationName ?? "?"),
    Number(document.startTime ?? 0) / 1000,
    Number(document.duration ?? 0) / 1000,
    document.process?.serviceName ? String(document.process.serviceName) : undefined,
    hasError,
    attrs,
    document,
    errorEvents,
    events,
  );
}

export function normalizeJaegerSpans(documents: Iterable<RecordValue>): Map<string, NormSpan> {
  const spans = new Map<string, NormSpan>();
  for (const document of documents) {
    const span = normalizeJaegerSpan(document);
    if (span) spans.set(span.span_id, span);
  }
  return spans;
}
