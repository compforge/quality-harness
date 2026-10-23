import { expect, test } from "bun:test";
import { collectClusterCompute, collectWorkloadCompute, parseComputeQuantity, type ComputeOptions } from "../src/kubernetes/compute";
import { podCompute, summarizePods } from "../src/kubernetes/compute-quantity";
import { KubernetesClient } from "../src/kubernetes/client";
import { kubernetesHttpError } from "../src/errors";
import type { Resource, ResourceAccess } from "../src/kubernetes/resources";
import type { WorkloadInstance } from "@compforge/harness-common";

const options: ComputeOptions = { environment: "test", timeoutMs: 1000, maxNodes: 10, maxPods: 10, pageSize: 2 };
const node = (name = "node-1"): Resource => ({ metadata: { name, uid: name },
  status: { capacity: { cpu: "8", memory: "16Gi" }, allocatable: { cpu: "7500m", memory: "15Gi" },
    conditions: [{ type: "Ready", status: "True" }] } });
const pod = (name = "pod-1", phase = "Running"): Resource => ({ metadata: { namespace: "ns", name, uid: name,
  creationTimestamp: "2026-10-23T00:00:00Z" },
  spec: { nodeName: "node-1", containers: [{ name: "app", resources: { requests: { cpu: "500m", memory: "1.5Gi" },
    limits: { cpu: "1", memory: "2Gi" } } }] },
  status: { phase, conditions: [{ type: "Ready", status: "True" }], containerStatuses: [{ name: "app", restartCount: 2 }] } });
const sample = { timestamp: "2026-10-23T00:01:00Z", window: "30s" };
const metrics = (name = "pod-1"): Resource => ({ ...sample, metadata: { name, namespace: "ns" },
  containers: [{ name: "app", usage: { cpu: "125000000n", memory: "512Mi" } }] });
const instance: WorkloadInstance = { environment: "test", platform: "kubernetes", workload: "main", namespace: "ns", pod: "pod-1", uid: "pod-1" };
function access(): ResourceAccess {
  return {
    async get(ref) { return ref.apiVersion === "v1" ? pod(ref.name) : metrics(ref.name); },
    async list(query) {
      return { items: query.apiVersion !== "v1" ? [{ ...sample, metadata: { name: "node-1" }, usage: { cpu: "1", memory: "3Gi" } }]
        : query.resource === "nodes" ? [node()] : [pod()] };
    },
  };
}

test("quantities support fractional binary units, milli/nano and exponent notation; invalid is not zero", () => {
  for (const [input, expected] of [["1.5Gi", 1.5 * 2 ** 30], ["500m", 0.5], ["123n", 123e-9], ["1e3", 1000], ["0", 0]] as const) {
    expect(parseComputeQuantity(input)).toBeCloseTo(expected);
  }
  for (const input of ["", "bad", "-1", "Infinity", "1MiB", "100Ei", undefined]) {
    expect(() => parseComputeQuantity(input)).toThrow();
  }
});

test("effective requests include native sidecars, init peak, Pod-level overrides and overhead", () => {
  const input = pod();
  input.spec!.initContainers = [
    { name: "sidecar", restartPolicy: "Always", resources: { requests: { cpu: "250m", memory: "128Mi" } } },
    { name: "init", resources: { requests: { cpu: "2", memory: "256Mi" } } },
  ];
  input.spec!.overhead = { cpu: "100m", memory: "64Mi" };
  const result = podCompute(input);
  expect(result.requests.cpu).toBeCloseTo(2.35);
  expect(result.requests.memory).toBe(1.5 * 2 ** 30 + 192 * 2 ** 20);
  expect(result.containersWithoutLimits.cpu).toBe(2);
  expect(result.containers).toEqual(["app", "sidecar"]);
  input.spec!.resources = { requests: { cpu: "3" }, limits: { cpu: "4" } };
  expect(podCompute(input).requests.cpu).toBeCloseTo(3.1);
  expect(podCompute(input).configuredLimits.cpu).toBeCloseTo(4.1);
});

test("finished Pods retain inventory but do not reserve capacity; pending demand stays separate", () => {
  const pending = pod("pending", "Pending"); delete pending.spec!.nodeName;
  const result = summarizePods([podCompute(pod()), podCompute(pod("done", "Succeeded")), podCompute(pending)]);
  expect(result).toMatchObject({ pods: 3, activePods: 2, scheduledPods: 1, requests: { cpu: 1 }, scheduledRequests: { cpu: 0.5 } });
});

test("cluster view explicitly omits namespace while normal client retains default scope", async () => {
  const calls: unknown[] = [];
  const client = new KubernetesClient({ namespace: "ns" }, undefined, undefined, undefined, {
    ...access(), async list(query, opts) { calls.push([query.namespace, opts?.signal]); return { items: [] }; },
  });
  await client.list({ apiVersion: "v1", resource: "pods" });
  const cluster = client.clusterResources();
  await cluster.list({ apiVersion: "v1", resource: "nodes" });
  expect(calls).toEqual([["ns", client.signal], [undefined, client.signal]]);
  await client.dispose();
  expect(() => cluster.list({ apiVersion: "v1", resource: "nodes" })).toThrow("disposed");
});

test("cluster permission denial is expected evidence; namespace Workload collection still succeeds", async () => {
  const api: ResourceAccess = { ...access(), async list() { throw kubernetesHttpError(403); } };
  const cluster = await collectClusterCompute(api, options);
  for (const read of [cluster.nodes, cluster.pods, cluster.usage]) {
    expect(read.status).toBe("unavailable");
    expect(read.issues[0]).toMatchObject({ kind: "permission_denied", code: 403, message: "Permission denied" });
  }
  expect(cluster.summary.capacity).toBeUndefined();
  expect(cluster.summary.usage).toBeUndefined();
  const workload = await collectWorkloadCompute(api, [instance], options);
  expect(workload.pods.status).toBe("complete");
  expect(workload.summary.usage?.cpu).toBe(0.125);
});

test("partial cluster permissions retain independent capacity and metric evidence", async () => {
  const base = access();
  const result = await collectClusterCompute({ ...base, list: async (q, o) => {
    if (q.resource === "pods") throw kubernetesHttpError(403);
    return base.list(q, o);
  } }, options);
  expect(result.summary.capacity?.cpu).toBe(8);
  expect(result.summary.allocatable?.cpu).toBe(7.5);
  expect(result.pods.status).toBe("unavailable");
  expect(result.summary.usage?.memory).toBe(3 * 2 ** 30);
});

test("pagination is bounded and does not silently turn a sample into a complete cluster", async () => {
  const calls: number[] = [];
  const result = await collectClusterCompute({ ...access(), async list(q) {
    if (q.apiVersion !== "v1" || q.resource !== "nodes") return { items: [] };
    calls.push(q.limit!);
    return q.continue ? { items: [node("b")], metadata: { continue: "more" } }
      : { items: [node("a")], metadata: { continue: "next" } };
  } }, { ...options, maxNodes: 2, pageSize: 1 });
  expect(calls).toEqual([1, 1]);
  expect(result.nodes.status).toBe("partial");
  expect(result.nodes.items).toHaveLength(2);
  expect(result.summary.capacity?.cpu).toBe(16);
  expect(result.usage.status).toBe("partial");
  expect(result.summary.usage).toBeUndefined();
});

test("later page denial preserves earlier nodes and no namespace fallback is attempted", async () => {
  const result = await collectClusterCompute({ ...access(), async list(q) {
    expect(q.namespace).toBeUndefined();
    if (q.resource !== "nodes" || q.apiVersion !== "v1") return { items: [] };
    if (q.continue) throw kubernetesHttpError(403);
    return { items: [node()], metadata: { continue: "next" } };
  } }, options);
  expect(result.nodes.status).toBe("partial");
  expect(result.nodes.issues[0].kind).toBe("permission_denied");
  expect(result.nodes.items).toHaveLength(1);
});

test("duplicate Workload aliases and container defaults count the whole Pod once", async () => {
  let gets = 0;
  const base = access();
  const result = await collectWorkloadCompute({ ...base, async get(q, o) { gets++; return base.get(q, o); } },
    [instance, { ...instance, workload: "alias", container: "app" }], options);
  expect(gets).toBe(2);
  expect(result.workloads).toHaveLength(2);
  expect(result.pods.items).toHaveLength(1);
  expect(result.summary.pods?.requests.cpu).toBe(0.5);
  expect(result.usage.items[0]).toMatchObject({ timestamp: sample.timestamp, window: "30s", uid: "pod-1" });
});

test("missing metrics never becomes zero usage; requests and limits remain available", async () => {
  const result = await collectWorkloadCompute({ ...access(), async get(q) {
    if (q.apiVersion !== "v1") throw kubernetesHttpError(403);
    return pod();
  } }, [instance], options);
  expect(result.pods.status).toBe("complete");
  expect(result.usage.status).toBe("unavailable");
  expect(result.summary.usage).toBeUndefined();
  expect(result.summary.pods?.configuredLimits.cpu).toBe(1);
});

test("Pod replacement and stale/incomplete container metrics do not join to a different incarnation", async () => {
  for (const variant of ["uid", "time", "containers"] as const) {
    const result = await collectWorkloadCompute({ ...access(), async get(q) {
      if (q.apiVersion === "v1") return variant === "uid" ? { ...pod(), metadata: { ...pod().metadata, uid: "replacement" } } : pod();
      return variant === "time" ? { ...metrics(), timestamp: "2026-10-22T00:00:00Z" } : { ...metrics(), containers: [] };
    } }, [instance], options);
    expect(result.usage.items).toHaveLength(0);
    expect(result.summary.usage).toBeUndefined();
  }
});

test("invalid cluster quantities keep other evidence; no credentials leak in acquisition errors", async () => {
  const result = await collectClusterCompute({ ...access(), async list(q) {
    if (q.resource === "nodes" && q.apiVersion === "v1") return { items: [{ ...node(), status: {} }] };
    throw new Error("https://user:secret@private.example/");
  } }, options);
  expect(result.nodes.status).toBe("partial");
  expect(result.summary.capacity).toBeUndefined();
  expect(JSON.stringify(result)).not.toContain("secret");
  expect(result.pods.issues[0].kind).toBe("operation_failed");
});

test("cancellation preserves caller reason and stops additional reads", async () => {
  const controller = new AbortController(), reason = new Error("cancelled by caller");
  let calls = 0;
  await expect(collectClusterCompute({ ...access(), async list(_q, request) {
    calls++; expect(request?.signal).toBe(controller.signal); controller.abort(reason); throw reason;
  } }, { ...options, signal: controller.signal })).rejects.toBe(reason);
  expect(calls).toBe(1);
});

test("one deadline covers all reads and does not silently restart on another source", async () => {
  let calls = 0;
  const result = await collectClusterCompute({ ...access(), async list() {
    calls++; await new Promise(resolve => setTimeout(resolve, 15)); return { items: [node()] };
  } }, { ...options, timeoutMs: 5 });
  expect(calls).toBe(1);
  expect(result.pods.issues[0].kind).toBe("timeout");
  expect(result.usage.issues[0].kind).toBe("timeout");
});

test("invalid budgets and mixed environments fail before any remote access", async () => {
  await expect(collectClusterCompute(access(), { ...options, maxPods: 0 })).rejects.toMatchObject({ kind: "invalid_argument" });
  await expect(collectWorkloadCompute(access(), [{ ...instance, environment: "other" }], options)).rejects.toMatchObject({ kind: "invalid_argument" });
});

// An empty Metrics API list is not proof of zero usage when Node discovery is forbidden.
test("empty metrics with unknown cluster inventory stays unknown", async () => {
  const result = await collectClusterCompute({ ...access(), async list(q) {
    if (q.apiVersion === "v1") throw kubernetesHttpError(403);
    return { items: [] };
  } }, options);
  expect(result.summary.usage).toBeUndefined();
});

test("empty filtered pages can advance, but repeated continuation cannot loop forever", async () => {
  for (const repeat of [false, true]) {
    let pages = 0;
    const result = await collectClusterCompute({ ...access(), async list(q) {
      if (q.resource !== "nodes" || q.apiVersion !== "v1") return { items: [] };
      pages++;
      return !q.continue || repeat ? { items: [], metadata: { continue: "next" } } : { items: [node()] };
    } }, options);
    expect(pages).toBe(2);
    expect(result.nodes.status).toBe(repeat ? "partial" : "complete");
    expect(result.nodes.items.length).toBe(repeat ? 0 : 1);
  }
});
