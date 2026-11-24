import { MetricDBError } from "../errors";
import type { MetricDBResult } from "./model";

export function decodeEnvelope(status: number, body: Uint8Array): Record<string, unknown> {
  // Remote bodies may contain credentials or proxy diagnostics; do not echo them into evidence.
  if (status !== 200) {
    const kinds = { 401: "authentication_failed", 403: "permission_denied", 404: "resource_not_found",
      405: "unsupported_operation", 501: "unsupported_operation" } as const;
    throw new MetricDBError(`MetricDB HTTP ${status}`, {
      kind: kinds[status as keyof typeof kinds] ?? "operation_failed", code: status,
    });
  }
  let payload: unknown;
  try { payload = JSON.parse(new TextDecoder().decode(body)); }
  catch { throw new MetricDBError("MetricDB returned invalid JSON", { kind: "invalid_response" }); }
  if (!payload || typeof payload !== "object") throw new MetricDBError("Invalid MetricDB response", { kind: "invalid_response" });
  const value = payload as Record<string, unknown>;
  if (value.status !== "success") throw new MetricDBError("MetricDB query failed", { kind: "operation_failed" });
  return value;
}

export function decodeResponse(status: number, body: Uint8Array): MetricDBResult {
  const value = decodeEnvelope(status, body);
  const data = value.data as MetricDBResult["data"] | undefined;
  if (!data || typeof data.resultType !== "string" || !Array.isArray(data.result))
    throw new MetricDBError("Invalid MetricDB result data", {kind: "invalid_response"});
  const messages = (key: string): string[] => {
    const items = value[key] ?? [];
    if (!Array.isArray(items) || items.some(item => typeof item !== "string")) throw new MetricDBError(`Invalid MetricDB ${key}`, {kind: "invalid_response"});
    return items;
  };
  if (value.isPartial !== undefined && typeof value.isPartial !== "boolean")
    throw new MetricDBError("Invalid MetricDB partial result flag", {kind: "invalid_response"});
  const warnings = messages("warnings");
  if (value.isPartial === true) warnings.push("Metric backend returned a partial result");
  return {data, warnings, infos: messages("infos"),
    ...(value.isPartial === undefined ? {} : {isPartial: value.isPartial as boolean})};
}

