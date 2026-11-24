import { raceSignal } from "race-signal";
import { TimeoutError, ToolboxError } from "../errors";
import type { HttpApi } from "../http/types";
import type { MetricDBKind, MetricDBReader, ScrapeReader, MetricDBResult, MetricDBBackend } from "./model";
import { backendFor, detectBackend } from "./backend";
import { scrapeConfigs, scrapeTargets } from "./scrape";
import { decodeEnvelope, decodeResponse } from "./response";

export interface MetricEndpoint {
  url: string;
  username?: string;
  password?: string;
}

export interface MetricDBOptions extends MetricEndpoint {
  kind?: MetricDBKind;
  collector?: MetricEndpoint;
  headers?: Readonly<Record<string, string>>;
}

type MetricHttp = HttpApi | ((url: string) => Promise<HttpApi>);

function connection(endpoint: MetricEndpoint, headers?: Readonly<Record<string, string>>) {
  const url = new URL(endpoint.url);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new ToolboxError("MetricDB URL requires HTTP(S), without credentials, query or fragment", {kind: "invalid_argument"});
  if (!!endpoint.username !== !!endpoint.password)
    throw new ToolboxError("MetricDB username and password must be provided together", {kind: "invalid_argument"});
  return {url: url.toString().replace(/\/$/, ""), headers: endpoint.username
    ? {...headers, Authorization: "Basic " + Buffer.from(endpoint.username + ":" + endpoint.password).toString("base64")}
    : {...headers}};
}

/**
 * @spec Reads existing Prometheus history at the requested time; never scrapes exporters or follows redirects.
 * @why Backend adapters encapsulate protocol differences; consumers retain ownership of query meaning.
 */
export class MetricDBClient implements MetricDBReader, ScrapeReader {
  private readonly queryEndpoint;
  private backend: MetricDBBackend;
  constructor(private readonly http: MetricHttp, private readonly options: MetricDBOptions) {
    this.queryEndpoint = connection(options, options.headers);
    this.backend = backendFor(options.kind ?? "prometheus");
  }
  get url() { return this.queryEndpoint.url; }
  get kind() { return this.backend.kind; }
  get scrapeEndpoint(): "collector" | "metricdb" { return this.options.collector ? "collector" : "metricdb"; }

  private borrow(url: string, signal?: AbortSignal) {
    signal?.throwIfAborted();
    if (typeof this.http !== "function") return this.http;
    const pending = this.http(url);
    return signal ? raceSignal(pending, signal) : pending;
  }
  static async connect(http: MetricHttp, options: MetricDBOptions, signal?: AbortSignal): Promise<MetricDBClient> {
    return new MetricDBClient(http, options).connectQuery(signal);
  }
  async connectQuery(signal?: AbortSignal): Promise<this> {
    if (!this.options.kind) {
      const http = await this.borrow(this.url, signal);
      this.backend = backendFor(await detectBackend(http, this.url, this.queryEndpoint.headers, signal));
    }
    return this;
  }

  async getScrapeConfig(signal?: AbortSignal, onYaml?: (yaml: string) => void) {
    const response = await this.scrapeResponse("status/config", signal);
    return scrapeConfigs(decodeEnvelope(response.status, response.body).data, onYaml);
  }
  async listScrapeTargets({ state = "any" }: { state?: "active" | "dropped" | "any" } = {}, signal?: AbortSignal, onBody?: (body: Uint8Array) => void) {
    if (!["active", "dropped", "any"].includes(state)) throw new ToolboxError("Invalid scrape target state", {kind: "invalid_argument"});
    const response = await this.scrapeResponse(`targets?state=${state}`, signal);
    if (response.status === 200) onBody?.(response.body);
    return scrapeTargets(decodeEnvelope(response.status, response.body).data, state);
  }
  private async scrapeResponse(path: string, signal?: AbortSignal) {
    try {
      signal?.throwIfAborted();
      // @rule An explicit collector owns its credentials even when it shares the query origin.
      const endpoint = this.options.collector ? connection(this.options.collector) : this.queryEndpoint;
      const http = await this.borrow(endpoint.url, signal);
      return await http.request(`${endpoint.url}/api/v1/${path}`, {headers: endpoint.headers, signal});
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
    const http = await this.borrow(this.url, signal);
    const response = await http.request(`${this.url}/api/v1/${api}?${new URLSearchParams(params)}`, {
      headers: this.queryEndpoint.headers, signal,
    });
    return decodeResponse(response.status, response.body);
  }
}

