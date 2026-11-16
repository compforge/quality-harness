import type { SearchEngine } from "../types";
import type { VdbFinding, VdbObservation } from "./model";
export interface OpenSearchHealthProbeResult {
  id: string;
  status: "ok" | "failed" | "unavailable";
  reason?: string;
  observation?: VdbObservation;
  raw?: unknown;
}
export interface OpenSearchHealthResult {
  backend: "opensearch";
  startedAt: string;
  finishedAt: string;
  status: "ok" | "partial" | "unavailable";
  probes: OpenSearchHealthProbeResult[];
  observations: VdbObservation[];
  findings: VdbFinding[];
  summary: string;
}
export interface OpenSearchHealthInput {
  search: SearchEngine;
  signal?: AbortSignal;
  timeoutMs?: number;
}
