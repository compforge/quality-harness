import { ToolboxError } from "../errors";
import type { MetricDBResult } from "./model";

export function decodeEnvelope(status: number, body: Uint8Array): Record<string, unknown> {
  // Remote bodies may contain credentials or proxy diagnostics; do not echo them into evidence.
  if (status !== 200) {
    const kinds = { 401: "authentication_failed", 403: "permission_denied", 404: "resource_not_found",
      405: "unsupported_operation", 501: "unsupported_operation" } as const;
    throw new ToolboxError(`MetricDB HTTP ${status}`, {
      kind: kinds[status as keyof typeof kinds] ?? "operation_failed", code: status,
    });
  }
  let payload: unknown;
  try { payload = JSON.parse(new TextDecoder().decode(body)); }
  catch { throw new ToolboxError("MetricDB returned invalid JSON", { kind: "invalid_response" }); }
  if (!payload || typeof payload !== "object") throw new ToolboxError("Invalid MetricDB response", { kind: "invalid_response" });
  const value = payload as Record<string, unknown>;
  if (value.status !== "success") throw new ToolboxError("MetricDB query failed", { kind: "operation_failed" });
  return value;
}

export function decodeResponse(status: number, body: Uint8Array): MetricDBResult {
  const value = decodeEnvelope(status, body);
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

