import type { MetricDBResult } from "./model";

export function decodeResponse(status: number, body: Uint8Array): MetricDBResult {
  // Remote bodies may contain credentials or proxy diagnostics; do not echo them into evidence.
  if (status !== 200) throw new Error(`MetricDB HTTP ${status}`);
  let payload: unknown;
  try { payload = JSON.parse(new TextDecoder().decode(body)); }
  catch { throw new Error("MetricDB returned invalid JSON"); }
  if (!payload || typeof payload !== "object") throw new Error("Invalid MetricDB response");
  const value = payload as Record<string, unknown>;
  if (value.status !== "success") throw new Error("MetricDB query failed");
  const data = value.data as MetricDBResult["data"] | undefined;
  if (!data || typeof data.resultType !== "string" || !Array.isArray(data.result))
    throw new Error("Invalid MetricDB result data");
  const messages = (key: string): string[] => {
    const items = value[key] ?? [];
    if (!Array.isArray(items) || items.some(item => typeof item !== "string")) throw new Error(`Invalid MetricDB ${key}`);
    return items;
  };
  if (value.isPartial !== undefined && typeof value.isPartial !== "boolean")
    throw new Error("Invalid MetricDB partial result flag");
  const warnings = messages("warnings");
  if (value.isPartial === true) warnings.push("Metric backend returned a partial result");
  return {data, warnings, infos: messages("infos"),
    ...(value.isPartial === undefined ? {} : {isPartial: value.isPartial as boolean})};
}

