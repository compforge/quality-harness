/// <reference path="./node-fetch.d.ts" />
import { KubeConfig } from "@kubernetes/client-node";
import fetch from "node-fetch/lib/index.js";
import { ConcurrencyPool } from "../concurrency";
import { KubernetesError, ToolboxError, kubernetesHttpError } from "../errors";
import type { KubectlOptions } from "./executor";

export interface Resource {
  metadata?: { name?: string; namespace?: string; uid?: string };
  spec?: { selector?: unknown };
  items?: Resource[];
  kind?: string;
}

export interface ResourceAccess {
  get(namespace: string, resource: string, name?: string, selector?: string): Promise<Resource>;
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
  const config = new KubeConfig();
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
  return {
    get: (namespace, resource, name, selector) => pool.run(async () => {
      const group = ["pods", "services"].includes(resource) ? "/api/v1" : "/apis/apps/v1";
      const path = group + "/namespaces/" + encodeURIComponent(namespace) + "/" + resource
        + (name ? "/" + encodeURIComponent(name) : "");
      const url = new URL(cluster.server.replace(/\/$/, "") + path);
      if (selector) url.searchParams.set("labelSelector", selector);
      const controller = new AbortController();
      const abort = () => controller.abort(signal.reason);
      signal.addEventListener("abort", abort, { once: true });
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, limits.timeoutMs);
      try {
        signal.throwIfAborted();
        const init = await config.applyToFetchOptions({});
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
        if (signal.aborted) throw signal.reason;
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
        signal.removeEventListener("abort", abort);
      }
    }, signal),
  };
}
