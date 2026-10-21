/// <reference path="./node-fetch.d.ts" />
import { KubeConfig } from "@kubernetes/client-node";
import type { Agent } from "node:http";
import type { Readable } from "node:stream";
import type { RequestOptions } from "node:https";
import fetch from "node-fetch/lib/index.js";
import { Headers } from "node-fetch";
import { clientKey } from "../client";
import { ConcurrencyPool } from "../concurrency";
import { KubernetesError, ToolboxError, kubernetesHttpError } from "../errors";
import { resourcePath, type Resource, type ResourceAccess, type ResourceList, type ResourceListQuery,
  type ResourceRef, type ResourceRequestOptions, type ResourceLimits } from "./resources";

export interface KubernetesConnectionOptions { kubeconfig?: string; context?: string }
interface PooledAgent { key: string; agent: Agent; users: number }

/**
 * @spec One root transport owns HTTP connections across resource kinds and namespaces.
 * @rule Credentials refresh per request; TLS/proxy changes retire the old pool after its active reads finish.
 * @rule Deadlines cover queueing, authentication and response consumption; failed requests are never replayed.
 * @see {@link docs/toolbox.md}
 */
export class KubernetesResourceTransport implements ResourceAccess {
  readonly #config = new KubeConfig();
  readonly #controller = new AbortController();
  readonly #pool: ConcurrencyPool;
  readonly #agents = new Set<PooledAgent>();
  readonly #server: string;
  readonly #signal: AbortSignal;
  #current?: PooledAgent;

  constructor(options: KubernetesConnectionOptions, signal: AbortSignal, private readonly limits: ResourceLimits) {
    if (![limits.timeoutMs, limits.concurrency, limits.maxBytes].every(value => Number.isSafeInteger(value) && value > 0)) {
      throw new KubernetesError("Resource limits must be positive integers", { kind: "invalid_argument" });
    }
    try {
      if (options.kubeconfig) this.#config.loadFromFile(options.kubeconfig);
      else this.#config.loadFromDefault();
      if (options.context) this.#config.setCurrentContext(options.context);
    } catch (cause) {
      throw new KubernetesError("Cannot load Kubernetes access configuration", { kind: "connection_failed", cause });
    }
    const cluster = this.#config.getCurrentCluster();
    if (!cluster) throw new KubernetesError("Kubernetes context has no cluster", { kind: "invalid_argument" });
    this.#server = cluster.server.replace(/\/$/, "");
    if (new URL(cluster.server).protocol === "http:") {
      this.#config.clusters = this.#config.clusters.map(item => item === cluster ? { ...item, skipTLSVerify: true } : item);
    }
    this.#signal = AbortSignal.any([signal, this.#controller.signal]);
    this.#pool = new ConcurrencyPool(limits.concurrency);
  }

  async get(ref: ResourceRef, options?: ResourceRequestOptions): Promise<Resource> {
    if (!ref.name) throw new KubernetesError("Resource name is required", { kind: "invalid_argument" });
    return this.#read(resourcePath(ref, ref.name), undefined, options);
  }

  async list(query: ResourceListQuery, options?: ResourceRequestOptions): Promise<ResourceList> {
    const params = new URLSearchParams();
    if (query.labelSelector) params.set("labelSelector", query.labelSelector);
    if (query.fieldSelector) params.set("fieldSelector", query.fieldSelector);
    if (query.continue) params.set("continue", query.continue);
    if (query.limit !== undefined) {
      if (!Number.isSafeInteger(query.limit) || query.limit <= 0) {
        throw new KubernetesError("Resource page limit must be a positive integer", { kind: "invalid_argument" });
      }
      params.set("limit", String(query.limit));
    }
    const value = await this.#read(resourcePath(query), params, options);
    if (!Array.isArray(value.items) || value.items.some(item => !isObject(item))) {
      throw new KubernetesError("Resource list lacks object items", { kind: "invalid_response" });
    }
    return value as unknown as ResourceList;
  }

  dispose(): void {
    this.#controller.abort(new Error("Kubernetes resource transport disposed"));
    for (const entry of this.#agents) entry.agent.destroy();
    this.#agents.clear();
    this.#current = undefined;
  }

  async #read(path: string, params: URLSearchParams | undefined, options?: ResourceRequestOptions): Promise<Resource> {
    const timeoutMs = options?.timeoutMs ?? this.limits.timeoutMs;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
      throw new KubernetesError("Resource timeout must be a positive integer", { kind: "invalid_argument" });
    }
    const caller = options?.signal ? AbortSignal.any([this.#signal, options.signal]) : this.#signal;
    const deadline = new AbortController();
    const signal = AbortSignal.any([caller, deadline.signal]);
    const timer = setTimeout(() => deadline.abort(), Math.min(timeoutMs, this.limits.timeoutMs));
    try {
      return await this.#pool.run(async () => {
        const request = await this.#authenticate(signal);
        if (signal.aborted) { (request.agent as Agent).destroy(); signal.throwIfAborted(); }
        const entry = this.#acquireAgent(request);
        try {
          const url = new URL(this.#server + path);
          if (params) url.search = params.toString();
          const headers = new Headers();
          for (const [name, value] of Object.entries(request.headers ?? {})) {
            for (const entry of Array.isArray(value) ? value : [value]) {
              if (entry !== undefined) headers.append(name, String(entry));
            }
          }
          if (request.auth) headers.set("Authorization", "Basic " + Buffer.from(request.auth).toString("base64"));
          const response = await fetch(url.href, { agent: entry.agent, headers, signal, size: this.limits.maxBytes });
          if (!response.ok) {
            (response.body as Readable).destroy();
            const error = kubernetesHttpError(response.status);
            throw new KubernetesError("Read Kubernetes resource " + path + " failed", { kind: error.kind, code: error.code });
          }
          const value: unknown = await response.json();
          signal.throwIfAborted();
          if (!isObject(value)) throw new KubernetesError("Resource response is not an object", { kind: "invalid_response" });
          return value as Resource;
        } finally {
          entry.users--;
          this.#retire(entry);
        }
      }, signal);
    } catch (cause) {
      if (caller.aborted) throw caller.reason;
      if (cause instanceof ToolboxError) throw cause;
      const type = cause && typeof cause === "object" && "type" in cause ? cause.type : undefined;
      const code = cause && typeof cause === "object" && "code" in cause && typeof cause.code === "string" ? cause.code : undefined;
      const tls = code !== undefined && ["CERT_HAS_EXPIRED", "DEPTH_ZERO_SELF_SIGNED_CERT",
        "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_VERIFY_LEAF_SIGNATURE", "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
        "ERR_TLS_CERT_ALTNAME_INVALID"].includes(code);
      throw new KubernetesError("Read Kubernetes resource " + path + " failed", {
        kind: deadline.signal.aborted ? "timeout" : type === "max-size" ? "limit_exceeded"
          : type === "invalid-json" ? "invalid_response" : tls ? "tls_verification_failed"
            : code === "ECONNRESET" ? "connection_lost" : "connection_failed", cause, code,
      });
    } finally { clearTimeout(timer); }
  }

  /**
   * @why The SDK's public authentication API also constructs a correctly configured proxy/TLS Agent.
   * Keep that adapter detail here; config owns credentials, this transport owns connection lifetime.
   */
  #authenticate(signal: AbortSignal): Promise<RequestOptions> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(signal.reason); return; }
      const abort = () => reject(signal.reason);
      signal.addEventListener("abort", abort, { once: true });
      const options: RequestOptions = { headers: {} };
      void this.#config.applyToHTTPSOptions(options).then(() => {
        signal.removeEventListener("abort", abort);
        if (signal.aborted) { (options.agent as Agent).destroy(); reject(signal.reason); }
        else resolve(options);
      }, error => { signal.removeEventListener("abort", abort); reject(error); });
    });
  }

  #acquireAgent(options: RequestOptions): PooledAgent {
    const fresh = options.agent as Agent & { keepAlive: boolean };
    const cluster = this.#config.getCurrentCluster();
    const tls = { ...options, agent: undefined, headers: undefined, auth: undefined };
    const key = clientKey("kubernetes-agent", { server: this.#server, proxy: cluster?.proxyUrl, tls });
    if (this.#current?.key === key) {
      fresh.destroy();
    } else {
      fresh.keepAlive = true;
      fresh.maxSockets = this.limits.concurrency;
      fresh.maxTotalSockets = this.limits.concurrency;
      fresh.maxFreeSockets = this.limits.concurrency;
      const previous = this.#current;
      this.#current = { key, agent: fresh, users: 0 };
      this.#agents.add(this.#current);
      if (previous) this.#retire(previous);
    }
    this.#current.users++;
    return this.#current;
  }

  #retire(entry: PooledAgent): void {
    if (entry !== this.#current && entry.users === 0) {
      entry.agent.destroy();
      this.#agents.delete(entry);
    }
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
