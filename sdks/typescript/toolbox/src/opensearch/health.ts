import { isOpenSearchReadApi, describeOpenSearchError } from "./protocol";
import type { SearchEngine } from "./types";
import { parseHealth, parseStats, parseShards, parseAllocation, parseDiskSettings, parseIndexBlocks,
  groupOpenSearchObservations, type OpenSearchObservation } from "./snapshot";
import { analyzeOpenSearch, openSearchCapacityConclusion, type OpenSearchFinding } from "./analysis";

export interface OpenSearchHealthProbeResult {
  id: string;
  status: "ok" | "failed" | "unavailable";
  reason?: string;
  observation?: OpenSearchObservation;
  raw?: unknown;
}
export interface OpenSearchHealthResult {
  backend: "opensearch";
  startedAt: string;
  finishedAt: string;
  status: "ok" | "partial" | "unavailable";
  probes: OpenSearchHealthProbeResult[];
  observations: OpenSearchObservation[];
  findings: OpenSearchFinding[];
  summary: string;
}
export interface OpenSearchHealthInput {
  search: SearchEngine;
  signal?: AbortSignal;
  timeoutMs?: number;
}
interface OpenSearchHealthProbe {
  id: string;
  path: string;
  query?: Record<string, unknown>;
  project?(raw: unknown): unknown;
  parse(raw: unknown): OpenSearchObservation;
}
function settingsRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

const CLUSTER_HEALTH_PROBE: OpenSearchHealthProbe = {
  id: "cluster-health",
  path: "/_cluster/health",
  parse: parseHealth,
};

const CLUSTER_STATS_PROBE: OpenSearchHealthProbe = {
  id: "cluster-stats",
  path: "/_cluster/stats",
  parse: parseStats,
};

const SHARD_STATE_PROBE: OpenSearchHealthProbe = {
  id: "shard-state",
  path: "/_cat/shards",
  query: { format: "json", bytes: "b" },
  parse: parseShards,
};
const NODE_ALLOCATION_PROBE: OpenSearchHealthProbe = {
  id: "node-allocation",
  path: "/_cat/allocation",
  query: { format: "json", bytes: "b" },
  parse: parseAllocation,
};

const CLUSTER_SETTINGS_PROBE: OpenSearchHealthProbe = {
  id: "cluster-settings",
  path: "/_cluster/settings",
  query: { include_defaults: true, flat_settings: true },
  project: raw => Object.fromEntries(["transient", "persistent", "defaults"].map(scope => [scope,
    Object.fromEntries(Object.entries(settingsRecord(settingsRecord(raw)[scope]))
      .filter(([key]) => key.startsWith("cluster.routing.allocation.disk."))),
  ])),
  parse: parseDiskSettings,
};

const INDEX_WRITE_BLOCKS_PROBE: OpenSearchHealthProbe = {
  id: "index-write-blocks",
  path: "/_all/_settings",
  query: {
    flat_settings: true,
    filter_path: "*.settings.index.blocks.read_only_allow_delete",
  },
  parse: parseIndexBlocks,
};

const PROBES: readonly OpenSearchHealthProbe[] = [CLUSTER_HEALTH_PROBE, NODE_ALLOCATION_PROBE, CLUSTER_SETTINGS_PROBE,
  INDEX_WRITE_BLOCKS_PROBE, SHARD_STATE_PROBE, CLUSTER_STATS_PROBE];

/** Bounded waiting does not cancel the borrowed client; its command owner closes outstanding transport work. */
async function bounded<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted();
  let abort: () => void = () => {};
  const stopped = new Promise<never>((_resolve, reject) => {
    abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
  });
  try { return await Promise.race([work, stopped]); }
  finally { signal.removeEventListener("abort", abort); }
}

export async function probeOpenSearchHealth({ search, signal, timeoutMs = 10_000 }: OpenSearchHealthInput): Promise<OpenSearchHealthResult> {
  const startedAt = new Date().toISOString();
  const deadline = AbortSignal.timeout(timeoutMs);
  const cancellation = signal ? AbortSignal.any([signal, deadline]) : deadline;
  const probes: OpenSearchHealthProbeResult[] = [];
  for (const spec of PROBES) {
    if (!isOpenSearchReadApi(search) || cancellation.aborted) {
      probes.push({ id: spec.id, status: "unavailable", reason: cancellation.aborted
        ? "健康探测已取消或超过时间预算" : "SearchEngine 不支持只读 cluster API" });
      continue;
    }
    try {
      const response = await bounded(search.request(spec.path, spec.query), cancellation);
      const raw = spec.project ? spec.project(response) : response;
      if (spec.id === "cluster-health" && !["green", "yellow", "red"].includes(String((raw as { status?: unknown })?.status))) {
        throw new Error("cluster health 响应缺少有效 status");
      }
      if (["node-allocation", "shard-state"].includes(spec.id) && !Array.isArray(raw)) {
        throw new Error(`${spec.id} 响应不是列表`);
      }
      const observation = spec.parse(raw);
      const incomplete = observation.kind === "opensearch-disk-settings"
        && [observation.low, observation.high, observation.floodStage].some(watermark => watermark.kind === "unknown");
      probes.push({ id: spec.id, status: incomplete ? "failed" : "ok", raw, observation,
        ...(incomplete ? { reason: "实时水位配置不完整，缺失项按常见默认值判断" } : {}) });
    } catch (error) {
      probes.push({ id: spec.id, status: "failed", reason: describeOpenSearchError(error) });
    }
  }
  const observations = probes.flatMap(probe => probe.observation ? [probe.observation] : []);
  const grouped = groupOpenSearchObservations(observations);
  const findings = analyzeOpenSearch(grouped);
  const status = probes.every(probe => probe.status === "ok") ? "ok" : observations.length ? "partial" : "unavailable";
  return { backend: "opensearch", startedAt, finishedAt: new Date().toISOString(), status,
    probes, observations, findings,
    summary: [`集群状态：${grouped.health?.status ?? "未知"}`, openSearchCapacityConclusion(grouped),
      ...findings.map(finding => finding.summary),
      ...(status === "ok" ? [] : ["健康证据不完整，缺失项不能解释为正常"])].join("；") };
}

export type { OpenSearchSnapshot, OpenSearchObservation, OpenSearchHealthObservation, OpenSearchAllocationObservation,
  OpenSearchAllocationNode, OpenSearchStatsObservation, OpenSearchShardObservation, OpenSearchDiskSettingsObservation,
  OpenSearchDiskWatermark, OpenSearchIndexBlocksObservation } from "./snapshot";
export { groupOpenSearchObservations } from "./snapshot";
export { analyzeOpenSearch, openSearchCapacityConclusion } from "./analysis";
export type { OpenSearchFinding, OpenSearchFindingKind } from "./analysis";
