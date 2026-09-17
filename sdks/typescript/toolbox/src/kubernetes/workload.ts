import type { Workload, WorkloadInstance } from "@compforge/harness-common";
import { KubernetesError } from "../errors";
import type { Resource, ResourceAccess } from "./resources";

function failure(message: string, kind: "invalid_argument" | "invalid_response" | "unsupported_operation"): never {
  throw new KubernetesError(message, { kind });
}

function selector(value: unknown, service = false): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return failure("Resource has no Pod selector", "unsupported_operation");
  }
  const input = value as { matchLabels?: Record<string, string>; matchExpressions?: {
    key: string; operator: string; values?: string[];
  }[] };
  const labels = service ? value as Record<string, string> : input.matchLabels ?? {};
  const terms = Object.entries(labels).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => {
    if (!key || /[,=()!\s]/.test(key) || typeof item !== "string" || /[,=()!\s]/.test(item)) {
      return failure("Invalid label selector", "invalid_argument");
    }
    return key + "=" + item;
  });
  for (const entry of service ? [] : input.matchExpressions ?? []) {
    const values = entry.values ?? [];
    if (!entry.key || /[,=()!\s]/.test(entry.key) || values.some(v => /[,=()!\s]/.test(v))) {
      return failure("Invalid selector expression", "invalid_response");
    }
    if ((entry.operator === "In" || entry.operator === "NotIn") && values.length) {
      terms.push(entry.key + (entry.operator === "In" ? " in (" : " notin (") + [...values].sort().join(",") + ")");
    } else if (entry.operator === "Exists" && !values.length) terms.push(entry.key);
    else if (entry.operator === "DoesNotExist" && !values.length) terms.push("!" + entry.key);
    else return failure("Unsupported selector expression", "invalid_response");
  }
  if (!terms.length) return failure("Resource has no Pod selector", "unsupported_operation");
  return terms.join(",");
}

/**
 * No readiness filter or replica selection; consumers own collection policy.
 * @rule environment is the caller's stable, non-secret target ID, independent of access routes.
 */
export async function resolveWorkload(access: ResourceAccess, workload: Workload, namespace: string, environment: string): Promise<WorkloadInstance[]> {
  if (!environment.trim()) return failure("Environment identity is required", "invalid_argument");
  if (!namespace.trim()) return failure("Workload namespace is required", "invalid_argument");
  const location = workload.location;
  let selected: string;
  let pods: Resource[];
  if (location.kind === "labels") {
    if (!Object.keys(location.labels).length) return failure("Pod labels must be nonempty", "invalid_argument");
    selected = selector(location.labels, true);
    const result = await access.get(namespace, "pods", undefined, selected);
    if (!Array.isArray(result.items)) return failure("Pod list lacks items", "invalid_response");
    pods = result.items;
  } else {
    const resource = location.kind === "service" ? "services" : ({
      Pod: "pods", Deployment: "deployments", StatefulSet: "statefulsets", DaemonSet: "daemonsets",
    } as const)[location.resource_kind];
    if (!resource) return failure("Unsupported workload resource", "unsupported_operation");
    if (!location.name.trim()) return failure("Resource name is required", "invalid_argument");
    const item = await access.get(namespace, resource, location.name);
    if (resource === "pods") pods = [item];
    else {
      selected = selector(item.spec?.selector, resource === "services");
      const result = await access.get(namespace, "pods", undefined, selected);
      if (!Array.isArray(result.items)) return failure("Pod list lacks items", "invalid_response");
      pods = result.items;
    }
  }
  return pods.map(pod => {
    const metadata = pod.metadata;
    if (!metadata?.name || !metadata.uid || metadata.namespace !== namespace) {
      return failure("Pod response lacks identity or has a different namespace", "invalid_response");
    }
    return { platform: "kubernetes" as const, environment, workload: workload.name,
      namespace, pod: metadata.name, uid: metadata.uid, container: workload.container };
  }).sort((a, b) => a.pod.localeCompare(b.pod) || a.uid.localeCompare(b.uid));
}
