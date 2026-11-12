import type { WorkloadInstance } from "@compforge/harness-common";
import { KubernetesError, type ErrorKind } from "../errors";
import type { KubernetesClient } from "./client";
import type { ResourceAccess, ResourceRequestOptions } from "./resources";

export interface KubernetesEnvironmentAccess {
  /** Explicit cluster-scoped resource view; Pod requests supply their namespace, Node requests do not. */
  resources: Pick<ResourceAccess, "get">;
  /** Borrow the caller's authorized exec channel; this function never creates or disposes a client. */
  exec: KubernetesClient["exec"];
}
export interface KubernetesEnvironmentIssue {
  source: "pod" | "pod.ip_forward" | "node" | `node.${string}`;
  kind: ErrorKind;
  message: string;
}
export interface KubernetesEnvironmentInspection {
  instance: WorkloadInstance;
  observedAt: string;
  status: "complete" | "partial" | "unavailable";
  /** Value in the sampled Pod's network namespace, not proof of the host setting. */
  podIpForward?: 0 | 1;
  container?: string;
  nodeName?: string;
  nodeInfo: Partial<Record<"operatingSystem" | "osImage" | "kernelVersion" | "architecture", string>>;
  issues: KubernetesEnvironmentIssue[];
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function text(value: unknown): string | undefined { return typeof value === "string" && value.trim() ? value : undefined; }
function issue(source: KubernetesEnvironmentIssue["source"], error: unknown): KubernetesEnvironmentIssue {
  const kind = error instanceof KubernetesError ? error.kind : "operation_failed";
  // Transport/command errors can contain credentials. Persist stable categories, never raw stderr or causes.
  return { source, kind, message: `Kubernetes environment read failed: ${kind}` };
}

/**
 * @spec Inspect only the supplied WorkloadInstance and its Node. Discovery and sampling belong to the caller.
 * @rule Reject a Pod already replaced at the identity read; later name-addressed exec is not atomic with that read.
 * @rule ip_forward is Pod-netns scoped; OS metadata comes from Node status.nodeInfo, never the container image.
 * @link docs/toolbox.md
 */
export async function inspectKubernetesEnvironment(access: KubernetesEnvironmentAccess, instance: WorkloadInstance,
  options: { timeoutMs: number; signal?: AbortSignal }): Promise<KubernetesEnvironmentInspection> {
  if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0
    || instance.platform !== "kubernetes" || [instance.environment, instance.workload, instance.namespace, instance.pod, instance.uid].some(value => !value?.trim())) {
    throw new KubernetesError("Environment inspection requires a WorkloadInstance and positive timeout", { kind: "invalid_argument" });
  }
  const deadline = performance.now() + options.timeoutMs;
  const request = (): ResourceRequestOptions => {
    options.signal?.throwIfAborted();
    const timeoutMs = Math.ceil(deadline - performance.now());
    if (timeoutMs <= 0) throw new KubernetesError("Environment inspection budget exhausted", { kind: "timeout" });
    return { timeoutMs, signal: options.signal };
  };
  const result: KubernetesEnvironmentInspection = { instance: { ...instance }, observedAt: new Date().toISOString(),
    status: "unavailable", nodeInfo: {}, issues: [] };
  let pod: Record<string, unknown>;
  try {
    pod = await access.resources.get({ apiVersion: "v1", resource: "pods", namespace: instance.namespace, name: instance.pod }, request());
    options.signal?.throwIfAborted();
    const meta = object(pod.metadata);
    if (meta.uid !== instance.uid || meta.name !== instance.pod || meta.namespace !== instance.namespace) {
      throw new KubernetesError("Pod identity changed or is missing", { kind: "invalid_response" });
    }
  } catch (error) {
    options.signal?.throwIfAborted();
    result.issues.push(issue("pod", error));
    return result;
  }
  const spec = object(pod.spec), status = object(pod.status);
  const containers = Array.isArray(spec.containers) ? spec.containers.map(object) : [];
  const container = instance.container ? containers.find(item => item.name === instance.container)
    : containers.length === 1 ? containers[0] : undefined;
  result.container = text(container?.name);
  result.nodeName = text(spec.nodeName);
  const [forwardingIssues, nodeIssues] = await Promise.all([
    (async (): Promise<KubernetesEnvironmentIssue[]> => {
      try {
        if (!result.container) throw new KubernetesError("Target container is absent or ambiguous", { kind: "invalid_argument" });
        const states = Array.isArray(status.containerStatuses) ? status.containerStatuses.map(object) : [];
        if (status.phase !== "Running" || !object(states.find(item => item.name === result.container)?.state).running) {
          throw new KubernetesError("Target container is not running", { kind: "unsupported_operation" });
        }
        const execution = await access.exec(instance.namespace, { pod: instance.pod, container: result.container },
          ["cat", "/proc/sys/net/ipv4/ip_forward"], request());
        options.signal?.throwIfAborted();
        if (!execution.ok) throw new KubernetesError("Could not read ip_forward", { kind: execution.timedOut ? "timeout" : "operation_failed" });
        const value = execution.stdout.trim();
        if (value !== "0" && value !== "1") throw new KubernetesError("Invalid ip_forward response", { kind: "invalid_response" });
        result.podIpForward = value === "1" ? 1 : 0;
        return [];
      } catch (error) {
        options.signal?.throwIfAborted();
        return [issue("pod.ip_forward", error)];
      }
    })(),
    (async (): Promise<KubernetesEnvironmentIssue[]> => {
      try {
        if (!result.nodeName) throw new KubernetesError("Pod nodeName is absent", { kind: "resource_not_found" });
        const node = await access.resources.get({ apiVersion: "v1", resource: "nodes", name: result.nodeName }, request());
        options.signal?.throwIfAborted();
        if (node.metadata?.name !== result.nodeName) throw new KubernetesError("Node identity is missing or mismatched", { kind: "invalid_response" });
        const info = object(object(node.status).nodeInfo);
        const issues: KubernetesEnvironmentIssue[] = [];
        for (const field of ["operatingSystem", "osImage", "kernelVersion", "architecture"] as const) {
          const value = text(info[field]);
          if (value) result.nodeInfo[field] = value;
          else issues.push({ source: `node.${field}`, kind: "invalid_response", message: `Node status.nodeInfo.${field} is missing` });
        }
        return issues;
      } catch (error) {
        options.signal?.throwIfAborted();
        return [issue("node", error)];
      }
    })(),
  ]);
  options.signal?.throwIfAborted();
  result.issues.push(...forwardingIssues, ...nodeIssues);
  result.status = !result.issues.length ? "complete" : result.podIpForward !== undefined || Object.keys(result.nodeInfo).length ? "partial" : "unavailable";
  return result;
}
