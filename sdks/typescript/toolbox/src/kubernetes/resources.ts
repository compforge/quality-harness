/// <reference path="./node-fetch.d.ts" />
import { PooledKubeConfig } from "./pooled-config";
import fetch from "node-fetch/lib/index.js";
import { ConcurrencyPool } from "../concurrency";
import { KubernetesError, ToolboxError, kubernetesHttpError } from "../errors";
import type { KubectlOptions, RunOptions } from "./executor";

export interface Resource {
  metadata?: { name?: string; namespace?: string; uid?: string };
  spec?: { selector?: unknown };
  items?: Resource[];
  kind?: string;
}

export interface ResourceAccess {
  get(namespace: string, resource: string, name?: string, selector?: string, options?: Pick<RunOptions, "signal" | "timeoutMs">): Promise<Resource>;
  dispose?(): void;
}

export interface ResourceLimits {
  timeoutMs: number;
  concurrency: number;
  maxBytes: number;
}

/** Native JSON reads use the same client-node auth/TLS path as native Pod logs. */
export function resourceAccess(
  options: KubectlOptions, signal: AbortSignal, limits: ResourceLimits,
): ResourceAccess {
  if (![limits.timeoutMs, limits.concurrency, limits.maxBytes].every(value => Number.isSafeInteger(value) && value > 0)) {
    throw new KubernetesError("Resource limits must be positive integers", { kind: "invalid_argument" });
  }
  const config = new PooledKubeConfig(limits.concurrency);
  try {
    if (options.kubeconfig) config.loadFromFile(options.kubeconfig);
    else config.loadFromDefault();
    if (options.context) config.setCurrentContext(options.context);
  } catch (cause) {
    throw new KubernetesError("Cannot load Kubernetes access configuration", { kind: "connection_failed", cause });
  }
  const cluster = config.getCurrentCluster();
  if (!cluster) throw new KubernetesError("Kubernetes context has no cluster", { kind: "invalid_argument" });
  if (new URL(cluster.server).protocol === "http:") {
    config.clusters = config.clusters.map(item => item === cluster ? { ...item, skipTLSVerify: true } : item);
  }
  const pool = new ConcurrencyPool(limits.concurrency);
  const disposal = new AbortController();
  const accessSignal = AbortSignal.any([signal, disposal.signal]);
  return {
    dispose: () => { disposal.abort(new Error("Kubernetes resource transport disposed")); config.dispose(); },
    get: (namespace, resource, name, selector, options) => {
      const requestSignal = options?.signal ? AbortSignal.any([accessSignal, options.signal]) : accessSignal;
      const timeoutMs = options?.timeoutMs ?? limits.timeoutMs;
      if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
        return Promise.reject(new KubernetesError("Resource timeout must be a positive integer", { kind: "invalid_argument" }));
      }
      return pool.run(async () => {
        const route = resourceRoute(resource);
        const group = route.group;
        resource = route.resource;
        const path = group + "/namespaces/" + encodeURIComponent(namespace) + "/" + resource
          + (name ? "/" + encodeURIComponent(name) : "");
        const url = new URL(cluster.server.replace(/\/$/, "") + path);
        if (selector) url.searchParams.set("labelSelector", selector);
        const controller = new AbortController();
        const abort = () => controller.abort(requestSignal.reason);
        requestSignal.addEventListener("abort", abort, { once: true });
        let timedOut = false;
        const timer = setTimeout(() => { timedOut = true; controller.abort(); }, Math.min(timeoutMs, limits.timeoutMs));
        try {
          requestSignal.throwIfAborted();
          const init = await authenticatedOptions(config, controller.signal);
          controller.signal.throwIfAborted();
          const response = await fetch(url.href, { ...init, signal: controller.signal, size: limits.maxBytes });
          if (!response.ok) {
            controller.abort();
            const error = kubernetesHttpError(response.status);
            throw new KubernetesError("Read " + resource + " in namespace " + namespace + " failed",
              { kind: error.kind, code: error.code });
          }
          return await response.json() as Resource;
        } catch (cause) {
          if (requestSignal.aborted) throw requestSignal.reason;
          if (cause instanceof ToolboxError) throw cause;
          const type = cause && typeof cause === "object" && "type" in cause ? cause.type : undefined;
          const code = cause && typeof cause === "object" && "code" in cause && typeof cause.code === "string" ? cause.code : undefined;
          const tls = code !== undefined && ["CERT_HAS_EXPIRED", "DEPTH_ZERO_SELF_SIGNED_CERT",
            "SELF_SIGNED_CERT_IN_CHAIN", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
            "UNABLE_TO_GET_ISSUER_CERT_LOCALLY", "ERR_TLS_CERT_ALTNAME_INVALID"].includes(code);
          throw new KubernetesError("Read " + resource + " in namespace " + namespace + " failed", {
            kind: timedOut ? "timeout" : type === "max-size" ? "limit_exceeded"
              : type === "invalid-json" ? "invalid_response" : tls ? "tls_verification_failed"
                : code === "ECONNRESET" ? "connection_lost" : "connection_failed",
            cause, code,
          });
        } finally {
          clearTimeout(timer);
          requestSignal.removeEventListener("abort", abort);
        }
      }, requestSignal);
    },
  };
}


const RESOURCE_ROUTES: Record<string, { group: string; resource: string }> = {
  pods: { group: "/api/v1", resource: "pods" },
  services: { group: "/api/v1", resource: "services" },
  configmaps: { group: "/api/v1", resource: "configmaps" },
  secrets: { group: "/api/v1", resource: "secrets" },
  events: { group: "/api/v1", resource: "events" },
  deployments: { group: "/apis/apps/v1", resource: "deployments" },
  statefulsets: { group: "/apis/apps/v1", resource: "statefulsets" },
  daemonsets: { group: "/apis/apps/v1", resource: "daemonsets" },
  replicasets: { group: "/apis/apps/v1", resource: "replicasets" },
};

export function resourceRoute(resource: string): { group: string; resource: string } {
  const apps = resource.endsWith(".apps");
  const name = apps ? resource.slice(0, -5) : resource;
  const route = Object.hasOwn(RESOURCE_ROUTES, name) ? RESOURCE_ROUTES[name] : undefined;
  if (!route || apps && route.group !== "/apis/apps/v1") throw new KubernetesError("Unsupported native resource: " + resource, { kind: "unsupported_operation" });
  return route;
}


/** Credential preparation shares the request deadline; late completion cannot dispatch an HTTP request. */
function authenticatedOptions(config: PooledKubeConfig, signal: AbortSignal): ReturnType<PooledKubeConfig["applyToFetchOptions"]> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    void config.applyToFetchOptions({}).then(
      options => { signal.removeEventListener("abort", abort); resolve(options); },
      error => { signal.removeEventListener("abort", abort); reject(error); },
    );
  });
}
