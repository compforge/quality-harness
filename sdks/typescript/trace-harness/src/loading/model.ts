/** Stable physical identity; details must come from the observed storage record. */
export interface EvidenceRef {
  trace_id: string;
  span_id: string;
  index?: string;
  document_id?: string;
}
export interface LoadConfig {
  lazy?: boolean;
  /** null (default) means full evidence when preloading. */
  fields?: readonly string[] | null;
  concurrency?: number;
  activeTraces?: number;
  maxTraceBytes?: number;
  cacheBytes?: number;
}
export function loadConfig(config: LoadConfig = {}): Required<LoadConfig> {
  const value = {
    lazy: config.lazy ?? true, fields: config.fields ?? null,
    concurrency: config.concurrency ?? 8, activeTraces: config.activeTraces ?? 4,
    maxTraceBytes: config.maxTraceBytes ?? 64 * 1024 * 1024,
    cacheBytes: config.cacheBytes ?? 128 * 1024 * 1024,
  };
  for (const key of ["concurrency", "activeTraces", "maxTraceBytes", "cacheBytes"] as const) {
    if (!Number.isSafeInteger(value[key]) || value[key] <= 0) throw new Error(`${key} must be a positive integer`);
  }
  return value;
}
export class EvidenceMissing extends Error {}
export class EvidenceTooLarge extends Error {}
