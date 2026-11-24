import { raceSignal } from "race-signal";
import { MetricDBError, ToolboxError } from "../errors";
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
  let url: URL;
  try { url = new URL(endpoint.url); }
  catch { throw new MetricDBError("MetricDB requires a valid absolute URL", {kind: "invalid_argument"}); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new MetricDBError("MetricDB URL requires HTTP(S), without credentials, query or fragment", {kind: "invalid_argument"});
  if (!!endpoint.username !== !!endpoint.password)
    throw new MetricDBError("MetricDB username and password must be provided together", {kind: "invalid_argument"});
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

  /** Borrow the same transport for a separately authenticated collector candidate. */
  forCollector(collector: MetricEndpoint): ScrapeReader {
    return new MetricDBClient(this.http, {...this.options, collector});
  }

  private borrow(url: string, signal?: AbortSignal) {
    if (typeof this.http !== "function") return this.http;
    const pending = this.http(url);
    return signal ? raceSignal(pending, signal) : pending;
  }
  static async connect(http: MetricHttp, options: MetricDBOptions, signal?: AbortSignal): Promise<MetricDBClient> {
    return new MetricDBClient(http, options).connectQuery(signal);
  }
  async connectQuery(signal?: AbortSignal): Promise<this> {
    if (!this.options.kind) {
      this.backend = backendFor(await detectBackend(path => this.send("query", path, signal)));
    }
    return this;
  }

  async getScrapeConfig(signal?: AbortSignal, onYaml?: (yaml: string) => void) {
    const response = await this.send("scrape", "status/config", signal);
    return scrapeConfigs(decodeEnvelope(response.status, response.body).data, onYaml);
  }
  async listScrapeTargets({ state = "any" }: { state?: "active" | "dropped" | "any" } = {}, signal?: AbortSignal, onBody?: (body: Uint8Array) => void) {
    if (!["active", "dropped", "any"].includes(state)) throw new MetricDBError("Invalid scrape target state", {kind: "invalid_argument"});
    const response = await this.send("scrape", `targets?state=${state}`, signal);
    if (response.status === 200) onBody?.(response.body);
    return scrapeTargets(decodeEnvelope(response.status, response.body).data, state);
  }
  private async send(operation: "query" | "scrape", path: string, signal?: AbortSignal) {
    try {
      signal?.throwIfAborted();
      // @rule An explicit collector owns its credentials and is validated only for scrape reads.
      const endpoint = operation === "scrape" && this.options.collector ? connection(this.options.collector) : this.queryEndpoint;
      const http = await this.borrow(endpoint.url, signal);
      return await http.request(`${endpoint.url}/api/v1/${path}`, {headers: endpoint.headers, signal});
    } catch (error) {
      const cause = signal?.aborted ? signal.reason : error;
      if (cause instanceof MetricDBError) throw cause;
      if (cause instanceof ToolboxError) {
        if (signal?.aborted) throw cause;
        throw new MetricDBError("MetricDB transport failed", {kind: cause.kind, code: cause.code, cause});
      }
      if (cause instanceof Error && (cause.name === "TimeoutError" || ("code" in cause && cause.code === "ETIMEDOUT"))) {
        throw new MetricDBError("MetricDB request timed out", {kind: "timeout", code: "ETIMEDOUT", cause});
      }
      // Explicit cancellation keeps the caller's reason, including non-Error values.
      if (signal?.aborted) throw cause;
      throw new MetricDBError("MetricDB transport failed", {kind: "connection_failed", cause});
    }
  }
  query(expression: string, time: number, signal?: AbortSignal): Promise<MetricDBResult> {
    if (!Number.isFinite(time)) throw new MetricDBError("MetricDB query requires a finite timestamp", {kind: "invalid_argument"});
    return this.request("query", {query: expression, time: String(time)}, signal);
  }
  queryRange(expression: string, start: number, end: number, step: number, signal?: AbortSignal): Promise<MetricDBResult> {
    if (![start, end, step].every(Number.isFinite) || end < start || step <= 0)
      throw new MetricDBError("MetricDB range requires finite start <= end and positive step", {kind: "invalid_argument"});
    return this.request("query_range", {query: expression, start: String(start), end: String(end), step: String(step)}, signal);
  }
  private async request(api: string, params: Record<string, string>, signal?: AbortSignal): Promise<MetricDBResult> {
    if (!params.query?.trim()) throw new MetricDBError("MetricDB expression must not be empty", {kind: "invalid_argument"});
    params = {...params, ...this.backend.queryParameters()};
    const response = await this.send("query", `${api}?${new URLSearchParams(params)}`, signal);
    return decodeResponse(response.status, response.body);
  }
}

