import type {
  OpenSearchAllocationNode,
  OpenSearchDiskSettingsObservation,
  OpenSearchDiskWatermark,
  OpenSearchSnapshot,
} from "./snapshot";

export type OpenSearchFindingKind =
  | "opensearch.cluster-red"
  | "opensearch.cluster-yellow"
  | "opensearch.disk-capacity-low"
  | "opensearch.disk-capacity-high"
  | "opensearch.disk-capacity-exhausted"
  | "opensearch.index-write-blocked"
  | "opensearch.unassigned-primary-shards"
  | "opensearch.unassigned-replica-shards";

export interface OpenSearchFinding {
  id: string; kind: OpenSearchFindingKind; severity: "warning" | "critical"; confidence: "high" | "medium";
  evidence: readonly { observationId: string; role: "supporting" }[];
  summary: string;
}

const FALLBACK_DISK_SETTINGS: OpenSearchDiskSettingsObservation = {
  id: "opensearch-disk-settings",
  kind: "opensearch-disk-settings",
  low: { raw: "85%", kind: "used-ratio", usedRatio: 0.85 },
  high: { raw: "90%", kind: "used-ratio", usedRatio: 0.9 },
  floodStage: { raw: "95%", kind: "used-ratio", usedRatio: 0.95 },
  source: "fallback-defaults",
};

function effectiveDiskSettings(observations: OpenSearchSnapshot): OpenSearchDiskSettingsObservation {
  const collected = observations.diskSettings;
  if (!collected) return FALLBACK_DISK_SETTINGS;
  const usedFallback = [collected.low, collected.high, collected.floodStage]
    .some((watermark) => watermark.kind === "unknown");
  return {
    ...collected,
    low: collected.low.kind === "unknown" ? FALLBACK_DISK_SETTINGS.low : collected.low,
    high: collected.high.kind === "unknown" ? FALLBACK_DISK_SETTINGS.high : collected.high,
    floodStage: collected.floodStage.kind === "unknown"
      ? FALLBACK_DISK_SETTINGS.floodStage
      : collected.floodStage,
    source: usedFallback ? "fallback-defaults" : "cluster-settings",
  };
}

function watermarkCrossed(node: OpenSearchAllocationNode, watermark: OpenSearchDiskWatermark): boolean {
  if (watermark.kind === "free-bytes") {
    return node.diskAvailableBytes !== undefined && node.diskAvailableBytes < watermark.freeBytes;
  }
  if (watermark.kind !== "used-ratio") return false;
  if (node.diskAvailableBytes !== undefined && node.diskTotalBytes !== undefined) {
    const ratioFree = node.diskTotalBytes * (1 - watermark.usedRatio);
    const requiredFree = watermark.maxHeadroomBytes === undefined
      ? ratioFree
      : Math.min(ratioFree, watermark.maxHeadroomBytes);
    return node.diskAvailableBytes < requiredFree;
  }
  return node.diskPercent !== undefined && node.diskPercent >= watermark.usedRatio * 100;
}

function firstCrossed(
  nodes: readonly OpenSearchAllocationNode[],
  watermark: OpenSearchDiskWatermark,
): OpenSearchAllocationNode | undefined {
  return nodes.find((node) => watermarkCrossed(node, watermark));
}

export function analyzeOpenSearch(observations: OpenSearchSnapshot): OpenSearchFinding[] {
  const findings: OpenSearchFinding[] = [];
  const health = observations.health;
  if (health?.status === "red") {
    findings.push({
      id: "cluster-red",
      kind: "opensearch.cluster-red",
      severity: "critical",
      confidence: "high",
      evidence: [{ observationId: health.id, role: "supporting" }],
      summary: "OpenSearch cluster health 为 red，存在 primary shard 不可用。",
    });
  } else if (health?.status === "yellow") {
    findings.push({
      id: "cluster-yellow",
      kind: "opensearch.cluster-yellow",
      severity: "warning",
      confidence: "high",
      evidence: [{ observationId: health.id, role: "supporting" }],
      summary: "OpenSearch cluster health 为 yellow，部分 replica shard 未正常分配。",
    });
  }

  const blocked = observations.indexBlocks?.readOnlyAllowDelete ?? [];
  if (blocked.length) {
    findings.push({
      id: "index-write-blocked",
      kind: "opensearch.index-write-blocked",
      severity: "critical",
      confidence: "high",
      evidence: [{ observationId: "opensearch-index-blocks", role: "supporting" }],
      summary: `${blocked.length} 个 index 已设置 read_only_allow_delete，写入当前受限。`,
    });
  }

  const nodes = (observations.allocation?.nodes ?? []).filter(node =>
    (node.diskTotalBytes !== undefined && node.diskTotalBytes > 0) || node.diskPercent !== undefined);
  const settings = effectiveDiskSettings(observations);
  const confidence = settings.source === "cluster-settings" ? "high" : "medium";
  const floodNode = firstCrossed(nodes, settings.floodStage);
  const highNode = firstCrossed(nodes, settings.high);
  const lowNode = firstCrossed(nodes, settings.low);
  if (floodNode) {
    findings.push({
      id: "disk-capacity-exhausted",
      kind: "opensearch.disk-capacity-exhausted",
      severity: "critical",
      confidence,
      evidence: [{ observationId: "opensearch-allocation", role: "supporting" }],
      summary: `${floodNode.node} 已越过 flood-stage 水位 ${settings.floodStage.raw}`
        + `${floodNode.diskPercent === undefined ? "" : `（使用率 ${floodNode.diskPercent}%）`}。`,
    });
  } else if (highNode) {
    findings.push({
      id: "disk-capacity-high",
      kind: "opensearch.disk-capacity-high",
      severity: "warning",
      confidence,
      evidence: [{ observationId: "opensearch-allocation", role: "supporting" }],
      summary: `${highNode.node} 已越过 high 水位 ${settings.high.raw}`
        + `${highNode.diskPercent === undefined ? "" : `（使用率 ${highNode.diskPercent}%）`}。`,
    });
  }

  if (!floodNode && !highNode && lowNode) findings.push({
    id: "disk-capacity-low", kind: "opensearch.disk-capacity-low",
    severity: "warning", confidence,
    evidence: [{ observationId: "opensearch-allocation", role: "supporting" }],
    summary: `${lowNode.node} 已越过 low 水位 ${settings.low.raw}，需要关注磁盘增长。`,
  });

  const shards = observations.shards;
  if ((shards?.unassignedPrimary ?? 0) > 0) {
    findings.push({
      id: "unassigned-primary-shards",
      kind: "opensearch.unassigned-primary-shards",
      severity: "critical",
      confidence: "high",
      evidence: [{ observationId: shards!.id, role: "supporting" }],
      summary: `存在 ${shards!.unassignedPrimary} 个 UNASSIGNED primary shard。`,
    });
  }
  if ((shards?.unassignedReplica ?? 0) > 0) {
    findings.push({
      id: "unassigned-replica-shards",
      kind: "opensearch.unassigned-replica-shards",
      severity: "warning",
      confidence: "high",
      evidence: [{ observationId: shards!.id, role: "supporting" }],
      summary: `存在 ${shards!.unassignedReplica} 个 UNASSIGNED replica shard。`,
    });
  }
  return findings;
}

export function openSearchCapacityConclusion(observations: OpenSearchSnapshot): string {
  const blocked = observations.indexBlocks?.readOnlyAllowDelete ?? [];
  if (blocked.length) return `写入已受限（${blocked.length} 个 index 为 read_only_allow_delete）`;
  const nodes = (observations.allocation?.nodes ?? []).filter(node =>
    (node.diskTotalBytes !== undefined && node.diskTotalBytes > 0) || node.diskPercent !== undefined);
  const maxPercent = Math.max(
    ...(nodes
      .map((node) => node.diskPercent)
      .filter((value): value is number => value !== undefined)),
    -1,
  );
  if (!nodes.length) return "无法判断：未取得 data node 磁盘利用率";
  const settings = effectiveDiskSettings(observations);
  const suffix = settings.source === "cluster-settings" ? "" : "；未取得实时水位，按常见默认值判断";
  if (firstCrossed(nodes, settings.floodStage)) {
    return `容量已越过 flood-stage ${settings.floodStage.raw}${maxPercent < 0 ? "" : `（最高 ${maxPercent}%）`}${suffix}`;
  }
  if (firstCrossed(nodes, settings.high)) {
    return `容量已越过 high 水位 ${settings.high.raw}${maxPercent < 0 ? "" : `（最高 ${maxPercent}%）`}${suffix}`;
  }
  return `容量未越过 high 水位 ${settings.high.raw}${maxPercent < 0 ? "" : `（最高 ${maxPercent}%）`}${suffix}`;
}

