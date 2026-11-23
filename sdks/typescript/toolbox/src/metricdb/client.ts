import { TimeoutError, ToolboxError } from "../errors";
import type { HttpApi } from "../http/types";
import type { MetricDBKind, MetricDBReader, ScrapeReader, MetricDBResult, MetricDBBackend } from "./model";
import { backendFor, detectBackend } from "./backend";
import { scrapeConfigs, scrapeTargets } from "./scrape";
import { decodeEnvelope, decodeResponse } from "./response";

/**
 * @spec Reads existing Prometheus history at the requested time; never scrapes exporters or follows redirects.
 * @why Backend adapters encapsulate protocol differences; consumers retain ownership of query meaning.
 */
export class MetricDBClient implements MetricDBReader, ScrapeReader {
  readonly url: string;
  readonly kind: MetricDBKind;
  private readonly backend: MetricDBBackend;
  constructor(private readonly http: HttpApi, options: {
    url: string; kind?: MetricDBKind; headers?: Readonly<Record<string, string>>;
  }) {
    const url = new URL(options.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
      throw new Error("MetricDB URL requires HTTP(S), without credentials, query or fragment");
    this.url = url.toString().replace(/\/$/, "");
    this.kind = options.kind ?? "prometheus";
    this.backend = backendFor(this.kind);
    this.headers = { ...options.headers };
  }
  static async connect(http: HttpApi, options: {
    url: string; headers?: Readonly<Record<string, string>>;
  }, signal?: AbortSignal): Promise<MetricDBClient> {
    const client = new MetricDBClient(http, options);
    const kind = await detectBackend(http, client.url, client.headers, signal);
    return new MetricDBClient(http, {...options, kind});
  }
  private readonly headers: Readonly<Record<string, string>>;
  async getScrapeConfig(signal?: AbortSignal) {
    const data = await this.scrapeData("status/config", signal);
    return scrapeConfigs(data);
  }
  async listScrapeTargets({ state = "any" }: { state?: "active" | "dropped" | "any" } = {}, signal?: AbortSignal) {
    if (!["active", "dropped", "any"].includes(state)) throw new ToolboxError("Invalid scrape target state", {kind: "invalid_argument"});
    return scrapeTargets(await this.scrapeData(`targets?state=${state}`, signal), state);
  }
  private async scrapeData(path: string, signal?: AbortSignal): Promise<unknown> {
    try {
      signal?.throwIfAborted();
      const response = await this.http.request(`${this.url}/api/v1/${path}`, { headers: this.headers, signal });
      return decodeEnvelope(response.status, response.body).data;
    } catch (error) {
      const cause = signal?.aborted ? signal.reason : error;
      if (cause instanceof ToolboxError) throw cause;
      if (cause instanceof Error && (cause.name === "TimeoutError" || ("code" in cause && cause.code === "ETIMEDOUT"))) {
        throw new TimeoutError("MetricDB collector request timed out", {cause});
      }
      // Explicit cancellation belongs to the caller and keeps its original reason.
      if (signal?.aborted) throw cause;
      throw new ToolboxError("MetricDB collector transport failed", {kind: "connection_failed", cause});
    }
  }
  query(expression: string, time: number, signal?: AbortSignal): Promise<MetricDBResult> {
    if (!Number.isFinite(time)) throw new Error("MetricDB query requires a finite timestamp");
    return this.request("query", {query: expression, time: String(time)}, signal);
  }
  queryRange(expression: string, start: number, end: number, step: number, signal?: AbortSignal): Promise<MetricDBResult> {
    if (![start, end, step].every(Number.isFinite) || end < start || step <= 0)
      throw new Error("MetricDB range requires finite start <= end and positive step");
    return this.request("query_range", {query: expression, start: String(start), end: String(end), step: String(step)}, signal);
  }
  private async request(api: string, params: Record<string, string>, signal?: AbortSignal): Promise<MetricDBResult> {
    if (!params.query?.trim()) throw new Error("MetricDB expression must not be empty");
    params = {...params, ...this.backend.queryParameters()};
    const response = await this.http.request(`${this.url}/api/v1/${api}?${new URLSearchParams(params)}`, {
      headers: this.headers, signal,
    });
    return decodeResponse(response.status, response.body);
  }
}

