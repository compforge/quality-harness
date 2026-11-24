import { MetricDBError } from "../errors";
import type { HttpResponse } from "../http/types";
import type { MetricDBKind, MetricDBBackend } from "./model";
import { decodeResponse } from "./response";
import { prometheus } from "./prometheus";
import { victoriaMetrics } from "./victoriametrics";

export function backendFor(kind: MetricDBKind): MetricDBBackend {
  switch (kind) {
    case "prometheus": return prometheus;
    case "victoriametrics": return victoriaMetrics;
    default: throw new MetricDBError("Unsupported MetricDB backend kind", {kind: "invalid_argument"});
  }
}

/**
 * @spec Detect query capability without relying on metric presence or URL shape.
 * @why VM buildinfo may report a Prometheus version; access failures are not backend hints.
 */
export async function detectBackend(read: (path: string) => Promise<HttpResponse>): Promise<MetricDBKind> {
    const response = await read(`query?${new URLSearchParams({query: "increase_prometheus(vector(0)[1m:1m])"})}`);
    // Only the explicit unsupported-function response selects Prometheus. Never echo backend bodies.
    if (response.status === 400 || response.status === 422) {
      let value: Record<string, unknown>;
      try { value = JSON.parse(new TextDecoder().decode(response.body)); }
      catch { throw new MetricDBError("Cannot identify Prometheus backend: invalid error response", {kind: "invalid_response", code: response.status}); }
      if (value?.status === "error" && value.errorType === "bad_data" && typeof value.error === "string"
        && value.error.includes('unknown function with name "increase_prometheus"')) return "prometheus";
      throw new MetricDBError("Cannot identify Prometheus backend: capability query rejected", {kind: "operation_failed", code: response.status});
    }
    const result = decodeResponse(response.status, response.body);
    if (result.data.resultType !== "vector") throw new MetricDBError("Cannot identify Prometheus backend: invalid capability result", {kind: "invalid_response"});
    return "victoriametrics";
}
