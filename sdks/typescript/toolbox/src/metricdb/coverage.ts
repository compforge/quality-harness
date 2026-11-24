import type { ScrapeConfig, ScrapeTarget } from "@compforge/harness-common";
import { parseDuration } from "../duration";
import { MetricDBError } from "../errors";
import type { MetricDBReader } from "./model";

export interface ScrapeGroup {
  pool?: string;
  state: ScrapeTarget["state"];
  health?: string;
  intervalSeconds?: number;
  intervalSource?: "target" | "config";
  count: number;
  lastScrape?: string;
}

function seconds(duration?: string): number | undefined {
  if (!duration) return;
  try { return parseDuration(duration) / 1000; } catch { return undefined; }
}

/** Group matched targets by job, health and effective interval. Keep the oldest reported last scrape. */
export function summarizeScrapeTargets(targets: readonly ScrapeTarget[], configs: readonly ScrapeConfig[]): ScrapeGroup[] {
  const jobs = new Map(configs.map(config => [config.jobName, config]));
  const groups = new Map<string, ScrapeGroup>();
  for (const target of targets) {
    const pool = target.scrapePool;
    const interval = seconds(target.scrapeInterval);
    const intervalSeconds = interval ?? seconds(pool && jobs.get(pool)?.scrapeInterval);
    const intervalSource = interval !== undefined ? "target" : intervalSeconds !== undefined ? "config" : undefined;
    const key = JSON.stringify([pool, target.state, target.health, intervalSeconds, intervalSource]);
    const group = groups.get(key) ?? {pool, state:target.state, health:target.health, intervalSeconds, intervalSource, count:0};
    group.count++;
    if (target.lastScrape && Number.isFinite(Date.parse(target.lastScrape)) &&
        (!group.lastScrape || Date.parse(target.lastScrape) < Date.parse(group.lastScrape))) group.lastScrape = target.lastScrape;
    groups.set(key, group);
  }
  return [...groups.values()];
}

const matcher = /[a-zA-Z_][a-zA-Z0-9_]*\s*(?:=~|!~|!=|=)\s*"(?:\\.|[^"\\])*"/;
const labelSet = `\\{\\s*(?:${matcher.source}(?:\\s*,\\s*${matcher.source})*\\s*,?\\s*)?\\}`;
const selector = `[a-zA-Z_:][a-zA-Z0-9_:]*(?:\\s*${labelSet})?`;
const literal = /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`|#[^\n]*/;
const windowOperand = new RegExp(literal.source + "|(" + selector + ")\\s*\\[\\s*\\$window\\s*\\]", "g");
const standaloneSelector = new RegExp("^" + selector + "$");

/** Inspect a standalone selector or named selector[$window] operands. Unsupported historical or subquery scopes stay unknown. */
export function observationSampleInputs(expression: string): {selectors:string[]; windowed:boolean} {
  if (standaloneSelector.test(expression.trim())) return {selectors:[expression.trim()], windowed:false};
  const syntax = expression.replace(new RegExp(literal.source, "g"), "");
  if (/\boffset\b|@|\[[^\]]*:/.test(syntax)) return {selectors:[], windowed:false};
  const selectors = [...new Set([...expression.matchAll(windowOperand)].flatMap(match => match[1] ? [match[1]] : []))];
  return {selectors, windowed:selectors.length > 0};
}

export interface MetricSampleCoverage {
  series: number;
  minSamples?: number;
  maxSamples?: number;
}

/** Count stored samples per series, then aggregate their distribution on the server to bound response size. */
export async function inspectMetricSamples(reader: Pick<MetricDBReader, "query">, selector: string, endTime: number,
  windowSeconds: number, signal?: AbortSignal): Promise<MetricSampleCoverage> {
  if (!standaloneSelector.test(selector) || !Number.isFinite(endTime) ||
      !Number.isSafeInteger(Math.round(windowSeconds * 1000)) || Math.round(windowSeconds * 1000) < 1)
    throw new MetricDBError("Invalid metric sample window or selector", {kind:"invalid_argument"});
  const expression = `count_values("samples", count_over_time(${selector}[${Math.round(windowSeconds * 1000)}ms]))`;
  const {data, warnings, isPartial} = await reader.query(expression, endTime, signal);
  if (isPartial || warnings.length || data.resultType !== "vector" || !Array.isArray(data.result))
    throw new MetricDBError("Metric sample coverage is incomplete", {kind:"invalid_response"});
  const coverage: MetricSampleCoverage = {series:0};
  for (const row of data.result) {
    const samples = Number(row?.metric?.samples), series = Number(row?.value?.[1]);
    if (!Number.isSafeInteger(samples) || samples < 1 || !Number.isSafeInteger(series) || series < 1)
      throw new MetricDBError("Invalid sample distribution", {kind:"invalid_response"});
    coverage.series += series;
    coverage.minSamples = Math.min(coverage.minSamples ?? samples, samples);
    coverage.maxSamples = Math.max(coverage.maxSamples ?? samples, samples);
  }
  return coverage;
}
