import { ScrapeConfig, ScrapeTarget } from "@compforge/harness-common";
import { ToolboxError } from "../errors";

function invalid(): never {
  throw new ToolboxError("Invalid MetricDB scrape response", { kind: "invalid_response" });
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}

function optionalString(value: unknown): string | undefined {
  if (value === undefined || typeof value === "string") return value;
  return invalid();
}

function labels(value: unknown): Record<string, string> | undefined {
  if (value == null) return undefined;
  const fields = record(value);
  if (Object.values(fields).some(v => typeof v !== "string")) return invalid();
  return fields as Record<string, string>;
}

export async function scrapeConfigs(data: unknown): Promise<ScrapeConfig[]> {
  const yaml = record(data).yaml;
  if (typeof yaml !== "string") return invalid();
  const { load } = await import("js-yaml");
  let parsed: unknown;
  // Parser diagnostics may contain credentials from the config.
  try { parsed = load(yaml); } catch { return invalid(); }
  const config = record(parsed);
  const global = config.global === undefined ? {} : record(config.global);
  const jobs = config.scrape_configs === undefined ? [] : config.scrape_configs;
  if (!Array.isArray(jobs)) return invalid();
  const interval = optionalString(global.scrape_interval);
  const timeout = optionalString(global.scrape_timeout);
  return jobs.map(item => {
    const job = record(item);
    if (typeof job.job_name !== "string" || !job.job_name.trim()) return invalid();
    return new ScrapeConfig({
      jobName: job.job_name,
      scrapeInterval: optionalString(job.scrape_interval) ?? interval,
      scrapeTimeout: optionalString(job.scrape_timeout) ?? timeout,
      metricsPath: optionalString(job.metrics_path),
      scheme: optionalString(job.scheme),
    });
  });
}

export function scrapeTargets(data: unknown, filter: "active" | "dropped" | "any"): ScrapeTarget[] {
  const groups = record(data);
  const result: ScrapeTarget[] = [];
  for (const state of ["active", "dropped"] as const) {
    if (filter !== "any" && filter !== state) continue;
    const group = groups[state + "Targets"];
    if (!Array.isArray(group)) return invalid();
    for (const entry of group) {
      const target = record(entry);
      const duration = target.lastScrapeDuration;
      if (duration !== undefined && (typeof duration !== "number" || !Number.isFinite(duration))) return invalid();
      if (state === "active" && typeof target.scrapeUrl !== "string") return invalid();
      if (state === "dropped" && !target.discoveredLabels) return invalid();
      result.push(new ScrapeTarget({
        state, discoveredLabels: labels(target.discoveredLabels), labels: labels(target.labels),
        scrapePool: optionalString(target.scrapePool), scrapeUrl: optionalString(target.scrapeUrl),
        health: optionalString(target.health), lastError: optionalString(target.lastError),
        lastScrape: optionalString(target.lastScrape), lastScrapeDuration: duration,
        scrapeInterval: optionalString(target.scrapeInterval), scrapeTimeout: optionalString(target.scrapeTimeout),
      }));
    }
  }
  return result;
}
