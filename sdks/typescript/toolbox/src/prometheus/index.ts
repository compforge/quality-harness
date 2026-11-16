import type { HttpApi } from "../http/types";

export type PrometheusKind = "prometheus" | "victoriametrics";
export interface PrometheusResult {
  data: { resultType: string; result: unknown };
  warnings: string[];
  infos: string[];
}
/** A borrowed query channel. The host owns HTTP pooling, credentials, TLS and disposal. */
export interface PrometheusReader {
  readonly url: string;
  readonly kind: PrometheusKind;
  query(expression: string, time: number, signal?: AbortSignal): Promise<PrometheusResult>;
  queryRange(expression: string, start: number, end: number, step: number, signal?: AbortSignal): Promise<PrometheusResult>;
}

/** @spec Reads existing Prometheus history; never scrapes exporters or follows redirects. */
export class PrometheusClient implements PrometheusReader {
  readonly url: string;
  readonly kind: PrometheusKind;
  constructor(private readonly http: HttpApi, options: {
    url: string; kind?: PrometheusKind; headers?: Readonly<Record<string, string>>;
  }) {
    const url = new URL(options.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new Error("Prometheus URL requires HTTP(S), without credentials, query or fragment");
    this.url = url.toString().replace(/\/$/, "");
    this.kind = options.kind ?? "prometheus";
    if (!["prometheus", "victoriametrics"].includes(this.kind)) throw new Error("Unsupported Prometheus backend kind");
    this.headers = { ...options.headers };
  }
  private readonly headers: Readonly<Record<string, string>>;
  query(expression: string, time: number, signal?: AbortSignal): Promise<PrometheusResult> {
    if (!Number.isFinite(time)) throw new Error("Prometheus query requires a finite timestamp");
    return this.request("query", {query: expression, time: String(time)}, signal);
  }
  queryRange(expression: string, start: number, end: number, step: number, signal?: AbortSignal): Promise<PrometheusResult> {
    if (![start, end, step].every(Number.isFinite) || end < start || step <= 0)
      throw new Error("Prometheus range requires finite start <= end and positive step");
    return this.request("query_range", {query: expression, start: String(start), end: String(end), step: String(step)}, signal);
  }
  private async request(api: string, params: Record<string, string>, signal?: AbortSignal): Promise<PrometheusResult> {
    if (!params.query?.trim()) throw new Error("Prometheus expression must not be empty");
    const response = await this.http.request(`${this.url}/api/v1/${api}?${new URLSearchParams(params)}`, {
      headers: this.headers, signal,
    });
    // Remote bodies may contain credentials or proxy diagnostics; do not echo them into evidence.
    if (response.status !== 200) throw new Error(`Prometheus HTTP ${response.status}`);
    let payload: unknown;
    try { payload = JSON.parse(new TextDecoder().decode(response.body)); }
    catch { throw new Error("Prometheus returned invalid JSON"); }
    if (!payload || typeof payload !== "object") throw new Error("Invalid Prometheus response");
    const value = payload as Record<string, unknown>;
    if (value.status !== "success") throw new Error("Prometheus query failed");
    const data = value.data as PrometheusResult["data"] | undefined;
    if (!data || typeof data.resultType !== "string" || !Array.isArray(data.result))
      throw new Error("Invalid Prometheus result data");
    const messages = (key: string): string[] => {
      const items = value[key] ?? [];
      if (!Array.isArray(items) || items.some(item => typeof item !== "string")) throw new Error(`Invalid Prometheus ${key}`);
      return items;
    };
    return {data, warnings: messages("warnings"), infos: messages("infos")};
  }
}
