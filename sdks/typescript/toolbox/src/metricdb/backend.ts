import type { HttpApi } from "../http/types";
import type { MetricDBKind, MetricDBBackend } from "./model";
import { decodeResponse } from "./response";
import { prometheus } from "./prometheus";
import { victoriaMetrics } from "./victoriametrics";

export function backendFor(kind: MetricDBKind): MetricDBBackend {
  switch (kind) {
    case "prometheus": return prometheus;
    case "victoriametrics": return victoriaMetrics;
    default: throw new Error("Unsupported MetricDB backend kind");
  }
}

/**
 * @spec Detect query capability without relying on metric presence or URL shape.
 * @why VM buildinfo may report a Prometheus version; access failures are not backend hints.
 */
export async function detectBackend(http: HttpApi, url: string,
  headers: Readonly<Record<string, string>>, signal?: AbortSignal): Promise<MetricDBKind> {
    signal?.throwIfAborted();
    const response = await http.request(`${url}/api/v1/query?${new URLSearchParams({
      query: "increase_prometheus(vector(0)[1m:1m])",
    })}`, {headers, signal});
    // Only the explicit unsupported-function response selects Prometheus. Never echo backend bodies.
    if (response.status === 400 || response.status === 422) {
      let value: Record<string, unknown>;
      try { value = JSON.parse(new TextDecoder().decode(response.body)); }
      catch { throw new Error("Cannot identify Prometheus backend: invalid error response"); }
      if (value?.status === "error" && value.errorType === "bad_data" && typeof value.error === "string"
        && value.error.includes('unknown function with name "increase_prometheus"')) return "prometheus";
      throw new Error("Cannot identify Prometheus backend: capability query rejected");
    }
    const result = decodeResponse(response.status, response.body);
    if (result.data.resultType !== "vector") throw new Error("Cannot identify Prometheus backend: invalid capability result");
    return "victoriametrics";
}
