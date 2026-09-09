import { bakeFeatures } from "../feature";
import type { FeatureRegistry } from "../feature/registry";
import { SERVICE_KIND, serviceSpec } from "../kinds/base";
import { TraceContext } from "../model/context";
import { Node } from "../model/node";
import type { KindSpec } from "../model/spec";
import { SpecSet } from "../model/spec";
import { spanErrorText, type NormSpan } from "../model/span";

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function assemble(
  spans: Map<string, NormSpan>,
  specset: SpecSet,
  featureRegistry?: FeatureRegistry,
): TraceContext {
  const kindOf = new Map<string, KindSpec | undefined>();
  for (const [spanId, span] of spans) kindOf.set(spanId, specset.classify(span));
  const primaries = new Set([...kindOf].filter(([, spec]) => spec).map(([spanId]) => spanId));

  const nearestPrimary = (spanId: string): string | undefined => {
    let current = spans.get(spanId)?.parent_span_id;
    while (current && spans.has(current)) {
      if (primaries.has(current)) return current;
      current = spans.get(current)?.parent_span_id;
    }
    return undefined;
  };

  const owned = new Map<string, string[]>();
  for (const spanId of spans.keys()) {
    if (primaries.has(spanId)) continue;
    const host = nearestPrimary(spanId);
    if (!host) continue;
    const members = owned.get(host) ?? [];
    members.push(spanId);
    owned.set(host, members);
  }

  const claimedBy = new Map<string, string[]>();
  const satelliteOf = new Map<string, string>();
  for (const primaryId of primaries) {
    const spec = kindOf.get(primaryId)!;
    const candidates = (owned.get(primaryId) ?? []).map((id) => spans.get(id)!);
    if (!spec.claims || candidates.length === 0) continue;
    for (const claimedId of spec.claims(spans.get(primaryId)!, candidates)) {
      const claimed = claimedBy.get(primaryId) ?? [];
      claimed.push(claimedId);
      claimedBy.set(primaryId, claimed);
      satelliteOf.set(claimedId, primaryId);
    }
  }

  const nodes: Node[] = [];
  const owner = new Map<string, string>();
  const makeNode = (
    primary: NormSpan,
    kind: string,
    satelliteIds: string[],
    facts: Record<string, unknown>,
  ): Node => {
    const spanIds = [primary.span_id, ...satelliteIds];
    const node = new Node({
      kind,
      name: primary.name,
      primary_span_id: primary.span_id,
      span_ids: spanIds,
      facts: { duration_ms: round(primary.dur_ms, 3), ...facts },
      start_ms: primary.start_ms,
      duration_ms: primary.dur_ms,
      service: primary.service,
      node_id: primary.span_id,
      error_span_ids: spanIds.filter((spanId) => spans.get(spanId)?.has_error),
    });
    for (const spanId of spanIds) owner.set(spanId, node.node_id);
    return node;
  };

  for (const primaryId of primaries) {
    const primary = spans.get(primaryId)!;
    const spec = kindOf.get(primaryId)!;
    const satelliteIds = claimedBy.get(primaryId) ?? [];
    const facts = spec.build?.(primary, satelliteIds.map((id) => spans.get(id)!)) ?? {};
    nodes.push(makeNode(primary, spec.kind, satelliteIds, facts));
  }

  for (const node of nodes) {
    let current = spans.get(node.primary_span_id)?.parent_span_id;
    while (current && spans.has(current)) {
      const host = owner.get(current);
      if (host && host !== node.node_id) {
        node.parent_node_id = host;
        break;
      }
      current = spans.get(current)?.parent_span_id;
    }
  }

  const groups = new Map<string, { host?: string; service?: string; members: string[] }>();
  for (const spanId of spans.keys()) {
    if (primaries.has(spanId) || satelliteOf.has(spanId)) continue;
    const host = nearestPrimary(spanId);
    const service = spans.get(spanId)?.service;
    const key = `${host ?? ""}\u0000${service ?? ""}`;
    const group = groups.get(key) ?? { host, service, members: [] };
    group.members.push(spanId);
    groups.set(key, group);
  }
  for (const { host, service, members } of groups.values()) {
    const groupedSpans = members.map((id) => spans.get(id)!);
    const start = Math.min(...groupedSpans.map((span) => span.start_ms));
    const end = Math.max(...groupedSpans.map((span) => span.end_ms));
    nodes.push(new Node({
      kind: SERVICE_KIND,
      name: service ?? "?",
      primary_span_id: members[0]!,
      span_ids: members,
      facts: {
        count: members.length,
        sum_ms: round(groupedSpans.reduce((sum, span) => sum + span.dur_ms, 0), 1),
      },
      start_ms: start,
      duration_ms: round(end - start, 3),
      service,
      node_id: `svc:${host ?? "root"}:${service ?? "undefined"}`,
      parent_node_id: host ? owner.get(host) : undefined,
      error_span_ids: groupedSpans.filter((span) => span.has_error).map((span) => span.span_id),
    }));
  }

  const realized = new Map<string, KindSpec>([[SERVICE_KIND, serviceSpec()]]);
  for (const spec of specset) if (!realized.has(spec.kind)) realized.set(spec.kind, spec);

  bakeFeatures(nodes, (spanId) => spans.get(spanId)?.attrs ?? {}, featureRegistry);
  for (const node of nodes) {
    node.brief = realized.get(node.kind)?.project?.(node) ?? [];
    if (node.has_error) node.error_text = spanErrorText(spans.get(node.error_anchor));
  }
  const traceId = String(spans.values().next().value?.raw.traceID ?? "?");
  return new TraceContext(traceId, spans, nodes, realized);
}
