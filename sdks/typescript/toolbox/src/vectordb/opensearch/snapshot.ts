/** Protocol observations and parsing; no I/O or consumer-specific evidence identity. */

export interface OpenSearchHealthObservation {
  id: "opensearch-health";
  kind: "opensearch-health";
  status: string;
  clusterName?: string;
  nodes: number;
  dataNodes: number;
  activePrimaryShards: number;
  activeShards: number;
  unassignedShards: number;
  initializingShards: number;
  relocatingShards: number;
  pendingTasks: number;
}

export interface OpenSearchAllocationNode {
  node: string;
  shards: number;
  diskUsedBytes?: number;
  diskAvailableBytes?: number;
  diskTotalBytes?: number;
  diskPercent?: number;
}

export interface OpenSearchAllocationObservation {
  id: "opensearch-allocation";
  kind: "opensearch-allocation";
  nodes: OpenSearchAllocationNode[];
}

export interface OpenSearchStatsObservation {
  id: "opensearch-stats";
  kind: "opensearch-stats";
  indices: number;
  documents: number;
  deletedDocuments: number;
  storeBytes: number;
  shards: number;
  primaryShards: number;
}

export interface OpenSearchShardObservation {
  id: "opensearch-shards";
  kind: "opensearch-shards";
  total: number;
  unassigned: number;
  unassignedPrimary: number;
  unassignedReplica: number;
}

export type OpenSearchDiskWatermark =
  | { raw: string; kind: "used-ratio"; usedRatio: number; maxHeadroomBytes?: number }
  | { raw: string; kind: "free-bytes"; freeBytes: number }
  | { raw: string; kind: "unknown" };

export interface OpenSearchDiskSettingsObservation {
  id: "opensearch-disk-settings";
  kind: "opensearch-disk-settings";
  low: OpenSearchDiskWatermark;
  high: OpenSearchDiskWatermark;
  floodStage: OpenSearchDiskWatermark;
  source: "cluster-settings" | "fallback-defaults";
}

export interface OpenSearchIndexBlocksObservation {
  id: "opensearch-index-blocks";
  kind: "opensearch-index-blocks";
  readOnlyAllowDelete: string[];
}

export interface OpenSearchSnapshot {
  health?: OpenSearchHealthObservation;
  allocation?: OpenSearchAllocationObservation;
  stats?: OpenSearchStatsObservation;
  shards?: OpenSearchShardObservation;
  diskSettings?: OpenSearchDiskSettingsObservation;
  indexBlocks?: OpenSearchIndexBlocksObservation;
  missing: string[];
}

export type OpenSearchObservation =
  | OpenSearchHealthObservation
  | OpenSearchAllocationObservation
  | OpenSearchStatsObservation
  | OpenSearchShardObservation
  | OpenSearchDiskSettingsObservation
  | OpenSearchIndexBlocksObservation;

export function groupOpenSearchObservations(
  observations: readonly OpenSearchObservation[],
): OpenSearchSnapshot {
  const find = <Kind extends OpenSearchObservation["kind"]>(kind: Kind) =>
    observations.find((item) => item.kind === kind) as Extract<OpenSearchObservation, { kind: Kind }> | undefined;
  const health = find("opensearch-health");
  const allocation = find("opensearch-allocation");
  const stats = find("opensearch-stats");
  const shards = find("opensearch-shards");
  const diskSettings = find("opensearch-disk-settings");
  const indexBlocks = find("opensearch-index-blocks");
  return {
    health,
    allocation,
    stats,
    shards,
    diskSettings,
    indexBlocks,
    missing: [
      ...(!health ? ["cluster health"] : []),
      ...(!allocation ? ["node allocation"] : []),
      ...(!stats ? ["cluster stats"] : []),
      ...(!shards ? ["shard state"] : []),
      ...(!diskSettings ? ["cluster disk watermarks（容量判读回退常见默认值）"] : []),
      ...(!indexBlocks ? ["index read_only_allow_delete blocks"] : []),
    ],
  };
}

export function parseHealth(raw: unknown): OpenSearchHealthObservation {
  const value = record(raw);
  return {
    id: "opensearch-health",
    kind: "opensearch-health",
    status: stringValue(value.status).toLowerCase(),
    clusterName: stringValue(value.cluster_name) || undefined,
    nodes: numberValue(value.number_of_nodes),
    dataNodes: numberValue(value.number_of_data_nodes),
    activePrimaryShards: numberValue(value.active_primary_shards),
    activeShards: numberValue(value.active_shards),
    unassignedShards: numberValue(value.unassigned_shards),
    initializingShards: numberValue(value.initializing_shards),
    relocatingShards: numberValue(value.relocating_shards),
    pendingTasks: numberValue(value.number_of_pending_tasks),
  };
}

export function parseStats(raw: unknown): OpenSearchStatsObservation {
  const indices = record(record(raw).indices);
  const docs = record(indices.docs);
  const store = record(indices.store);
  const shards = record(indices.shards);
  return {
    id: "opensearch-stats",
    kind: "opensearch-stats",
    indices: numberValue(indices.count),
    documents: numberValue(docs.count),
    deletedDocuments: numberValue(docs.deleted),
    storeBytes: numberValue(store.size_in_bytes),
    shards: numberValue(shards.total),
    primaryShards: numberValue(shards.primaries),
  };
}

export function parseShards(raw: unknown): OpenSearchShardObservation {
  const rows = Array.isArray(raw) ? raw.map(record) : [];
  const unassigned = rows.filter((row) => stringValue(row.state).toUpperCase() === "UNASSIGNED");
  const primary = unassigned.filter((row) => stringValue(row.prirep).toLowerCase() === "p").length;
  return {
    id: "opensearch-shards",
    kind: "opensearch-shards",
    total: rows.length,
    unassigned: unassigned.length,
    unassignedPrimary: primary,
    unassignedReplica: unassigned.length - primary,
  };
}

export function parseAllocation(raw: unknown): OpenSearchAllocationObservation {
  const rows = Array.isArray(raw) ? raw : [];
  const nodes = rows.map((rawRow): OpenSearchAllocationNode => {
    const row = record(rawRow);
    const optionalNumber = (value: unknown) => {
      if (value === null || value === undefined || value === "") return undefined;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    };
    return {
      node: stringValue(row.node) || stringValue(row.ip) || "unknown",
      shards: numberValue(row.shards),
      diskUsedBytes: optionalNumber(row["disk.used"]),
      diskAvailableBytes: optionalNumber(row["disk.avail"]),
      diskTotalBytes: optionalNumber(row["disk.total"]),
      diskPercent: optionalNumber(row["disk.percent"]),
    };
  });
  return {
    id: "opensearch-allocation",
    kind: "opensearch-allocation",
    nodes,
  };
}

function parseBytes(value: string | undefined): number | undefined {
  if (!value) return undefined;
  const matched = value.trim().toLowerCase().match(/^(\d+(?:\.\d+)?)\s*(b|kb|mb|gb|tb|pb)$/);
  if (!matched) return undefined;
  const unit = ["b", "kb", "mb", "gb", "tb", "pb"].indexOf(matched[2]!);
  return Number(matched[1]) * 1024 ** unit;
}

function parseWatermark(rawValue: unknown, maxHeadroom: unknown): OpenSearchDiskWatermark {
  const raw = stringValue(rawValue).trim();
  if (!raw) return { raw, kind: "unknown" };
  const headroom = parseBytes(stringValue(maxHeadroom).trim());
  if (raw.endsWith("%")) {
    const ratio = Number(raw.slice(0, -1)) / 100;
    if (Number.isFinite(ratio)) return { raw, kind: "used-ratio", usedRatio: ratio, maxHeadroomBytes: headroom };
  }
  const ratio = Number(raw);
  if (Number.isFinite(ratio) && ratio >= 0 && ratio <= 1) {
    return { raw, kind: "used-ratio", usedRatio: ratio, maxHeadroomBytes: headroom };
  }
  const freeBytes = parseBytes(raw);
  if (freeBytes !== undefined) return { raw, kind: "free-bytes", freeBytes };
  return { raw, kind: "unknown" };
}

function setting(raw: Record<string, unknown>, name: string): unknown {
  for (const scope of ["transient", "persistent", "defaults"]) {
    const value = record(raw[scope])[name];
    if (value !== undefined) return value;
  }
  return undefined;
}

export function parseDiskSettings(rawValue: unknown): OpenSearchDiskSettingsObservation {
  const raw = record(rawValue);
  const prefix = "cluster.routing.allocation.disk.watermark.";
  return {
    id: "opensearch-disk-settings",
    kind: "opensearch-disk-settings",
    low: parseWatermark(setting(raw, `${prefix}low`), setting(raw, `${prefix}low.max_headroom`)),
    high: parseWatermark(setting(raw, `${prefix}high`), setting(raw, `${prefix}high.max_headroom`)),
    floodStage: parseWatermark(
      setting(raw, `${prefix}flood_stage`),
      setting(raw, `${prefix}flood_stage.max_headroom`),
    ),
    source: "cluster-settings",
  };
}

export function parseIndexBlocks(rawValue: unknown): OpenSearchIndexBlocksObservation {
  const indices = record(rawValue);
  const readOnlyAllowDelete = Object.entries(indices).flatMap(([name, rawIndex]) => {
    const settings = record(record(rawIndex).settings);
    const blocks = record(record(settings.index).blocks);
    const value = settings["index.blocks.read_only_allow_delete"]
      ?? blocks.read_only_allow_delete;
    return value === true || value === "true" ? [name] : [];
  });
  return {
    id: "opensearch-index-blocks",
    kind: "opensearch-index-blocks",
    readOnlyAllowDelete,
  };
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}
