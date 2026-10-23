import { KubernetesError } from "../errors";
import type { ComputeQuantity, PodCompute, PodComputeSummary } from "./compute-model";
import type { Resource } from "./resources";

type ObjectValue = Record<string, unknown>;
export function object(value: unknown): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
}
function invalid(message: string): never {
  throw new KubernetesError(message, { kind: "invalid_response" });
}
export function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) return invalid(`Kubernetes response lacks ${field}`);
  return value;
}

/**
 * @why client-node 1.4 quantityToScalar uses BigInt for binary suffixes and rejects valid fractional Gi values.
 * @spec Quantities normalize to cores/bytes; invalid or unrepresentable values never become zero usage.
 */
export function parseComputeQuantity(value: unknown): number {
  if (typeof value !== "string") return invalid("Kubernetes quantity must be a string");
  const match = /^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([eE][+-]?\d+|[numkMGTPE]|[KMGTPE]i)?$/.exec(value);
  if (!match) return invalid("Invalid Kubernetes quantity");
  const suffix = match[2] ?? "";
  const factors: Record<string, number> = { "": 1, n: 1e-9, u: 1e-6, m: 1e-3, k: 1e3,
    M: 1e6, G: 1e9, T: 1e12, P: 1e15, E: 1e18,
    Ki: 2 ** 10, Mi: 2 ** 20, Gi: 2 ** 30, Ti: 2 ** 40, Pi: 2 ** 50, Ei: 2 ** 60 };
  const result = Number(match[1]) * (/^[eE][+-]?\d+$/.test(suffix) ? 10 ** Number(suffix.slice(1)) : factors[suffix]);
  if (!Number.isFinite(result) || result < 0 || result > Number.MAX_SAFE_INTEGER) {
    return invalid("Kubernetes quantity is outside the supported numeric range");
  }
  return result;
}
export const zero = (): ComputeQuantity => ({ cpu: 0, memory: 0 });
export function add(left: ComputeQuantity, right: ComputeQuantity): ComputeQuantity {
  return { cpu: left.cpu + right.cpu, memory: left.memory + right.memory };
}
function max(left: ComputeQuantity, right: ComputeQuantity): ComputeQuantity {
  return { cpu: Math.max(left.cpu, right.cpu), memory: Math.max(left.memory, right.memory) };
}
export function quantities(value: unknown, required = false): ComputeQuantity {
  const input = object(value);
  return { cpu: input.cpu === undefined && !required ? 0 : parseComputeQuantity(input.cpu),
    memory: input.memory === undefined && !required ? 0 : parseComputeQuantity(input.memory) };
}
export function sum(values: readonly ComputeQuantity[]): ComputeQuantity { return values.reduce(add, zero()); }

function containers(value: unknown): ObjectValue[] {
  if (!Array.isArray(value)) return invalid("Pod response lacks containers");
  return value.map(item => {
    const container = object(item);
    requiredString(container.name, "container.name");
    return container;
  });
}

/** Spec projection only: do not claim current cgroup allocation or scheduler admission headroom. */
export function podCompute(value: Resource): PodCompute {
  const meta = object(value.metadata), spec = object(value.spec), status = object(value.status);
  const app = containers(spec.containers);
  if (!app.length) return invalid("Pod must contain an application container");
  const init = containers(spec.initContainers ?? []);
  const effective = (field: "requests" | "limits"): ComputeQuantity => {
    let steady = sum(app.map(c => quantities(object(c.resources)[field])));
    let sidecars = zero(), peak = zero();
    for (const container of init) {
      const current = quantities(object(container.resources)[field]);
      if (container.restartPolicy === "Always") {
        sidecars = add(sidecars, current);
        steady = add(steady, current);
        peak = max(peak, sidecars);
      } else peak = max(peak, add(sidecars, current));
    }
    const result = max(steady, peak);
    const podLevel = object(object(spec.resources)[field]);
    const overhead = quantities(spec.overhead);
    for (const key of ["cpu", "memory"] as const) {
      if (podLevel[key] !== undefined) result[key] = parseComputeQuantity(podLevel[key]);
      if (field === "requests" || result[key] > 0) result[key] += overhead[key];
    }
    return result;
  };
  const without = zero();
  for (const container of [...app, ...init]) {
    const limits = object(object(container.resources).limits);
    for (const key of ["cpu", "memory"] as const) if (limits[key] === undefined) without[key]++;
  }
  const conditions = Array.isArray(status.conditions) ? status.conditions.map(object) : [];
  const states = [...(Array.isArray(status.containerStatuses) ? status.containerStatuses : []),
    ...(Array.isArray(status.initContainerStatuses) ? status.initContainerStatuses : [])].map(object);
  return {
    namespace: requiredString(meta.namespace, "metadata.namespace"), name: requiredString(meta.name, "metadata.name"),
    uid: requiredString(meta.uid, "metadata.uid"),
    createdAt: typeof meta.creationTimestamp === "string" ? meta.creationTimestamp : undefined,
    node: typeof spec.nodeName === "string" && spec.nodeName ? spec.nodeName : undefined,
    phase: requiredString(status.phase, "status.phase"),
    ready: conditions.some(c => c.type === "Ready" && c.status === "True"),
    restarts: states.reduce((n, c) => n + (typeof c.restartCount === "number" ? c.restartCount : 0), 0),
    containers: [...app, ...init.filter(c => c.restartPolicy === "Always")].map(c => String(c.name)),
    requests: effective("requests"), configuredLimits: effective("limits"), containersWithoutLimits: without,
  };
}
export function summarizePods(pods: readonly PodCompute[]): PodComputeSummary {
  const active = pods.filter(p => p.phase !== "Succeeded" && p.phase !== "Failed");
  const scheduled = active.filter(p => p.node);
  return { pods: pods.length, activePods: active.length, scheduledPods: scheduled.length,
    readyPods: active.filter(p => p.ready).length,
    requests: sum(active.map(p => p.requests)), scheduledRequests: sum(scheduled.map(p => p.requests)),
    configuredLimits: sum(active.map(p => p.configuredLimits)),
    containersWithoutLimits: sum(active.map(p => p.containersWithoutLimits)),
  };
}
