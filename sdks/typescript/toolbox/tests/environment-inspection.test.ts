import { expect, test } from "bun:test";
import type { WorkloadInstance } from "@compforge/harness-common";
import { KubernetesError } from "../src/errors";
import { inspectKubernetesEnvironment, type KubernetesEnvironmentAccess } from "../src/kubernetes/environment-inspection";
import type { Resource, ResourceRef } from "../src/kubernetes/resources";
import type { ExecResult } from "../src/kubernetes/executor";

const instance: WorkloadInstance = { platform: "kubernetes", environment: "test", workload: "runtime",
  namespace: "demo", pod: "runtime-one", uid: "uid-one", container: "runtime" };
const nodeInfo = { operatingSystem: "linux", osImage: "Kylin Linux Advanced Server V10", kernelVersion: "4.19.90-test", architecture: "amd64" };
const execution = (stdout = "0\n"): ExecResult => ({ ok: true, exitCode: 0, stdout, stderr: "", timedOut: false, durationMs: 1, command: [] });
function fixture() {
  const queries: ResourceRef[] = [];
  const calls: string[] = [];
  const pod: Resource = { metadata: { name: instance.pod, namespace: instance.namespace, uid: instance.uid },
    spec: { nodeName: "node-one", containers: [{ name: "sidecar" }, { name: "runtime" }] },
    status: { phase: "Running", containerStatuses: [{ name: "runtime", state: { running: {} } }] } };
  const node: Resource = { metadata: { name: "node-one" }, status: { nodeInfo: { ...nodeInfo } } };
  const access: KubernetesEnvironmentAccess = {
    resources: { get: async (ref, options) => {
      queries.push(ref);
      expect(options?.timeoutMs).toBeGreaterThan(0);
      expect(options?.timeoutMs).toBeLessThanOrEqual(5000);
      return ref.resource === "pods" ? pod : node;
    } },
    exec: async (namespace, target, command, options) => {
      calls.push(target.pod);
      expect(namespace).toBe("demo"); expect(target).toEqual({ pod: instance.pod, container: "runtime" });
      expect(command).toEqual(["cat", "/proc/sys/net/ipv4/ip_forward"]);
      expect(options?.timeoutMs).toBeGreaterThan(0);
      return execution();
    },
  };
  return { access, pod, node, calls, queries };
}

for (const architecture of ["amd64", "arm64"]) test(`inspect one caller-selected instance and only its Node: ${architecture}`, async () => {
  const f = fixture(); f.node.status = { nodeInfo: { ...nodeInfo, architecture } };
  const result = await inspectKubernetesEnvironment(f.access, instance, { timeoutMs: 5000 });
  expect(result).toMatchObject({ status: "complete", instance, container: "runtime", nodeName: "node-one", podIpForward: 0,
    nodeInfo: { ...nodeInfo, architecture }, issues: [] });
  expect(f.calls).toEqual([instance.pod]);
  expect(f.queries).toEqual([
    { apiVersion: "v1", resource: "pods", namespace: "demo", name: instance.pod },
    { apiVersion: "v1", resource: "nodes", name: "node-one" },
  ]);
});

test("Node denial retains Pod forwarding without leaking errors", async () => {
  const f = fixture(), get = f.access.resources.get;
  f.access.resources.get = async (ref, options) => ref.resource === "nodes"
    ? Promise.reject(new KubernetesError("secret credential", { kind: "permission_denied" })) : get(ref, options);
  const result = await inspectKubernetesEnvironment(f.access, instance, { timeoutMs: 5000 });
  expect(result).toMatchObject({ status: "partial", podIpForward: 0, nodeInfo: {}, issues: [{ source: "node", kind: "permission_denied" }] });
  expect(JSON.stringify(result)).not.toContain("secret credential");
});

for (const mode of ["denied", "timeout", "malformed", "stopped", "ambiguous"]) test(`forwarding gap retains Node metadata: ${mode}`, async () => {
  const f = fixture();
  f.access.exec = async () => {
    if (mode === "denied") throw new KubernetesError("secret", { kind: "permission_denied" });
    return mode === "timeout" ? { ...execution(), ok: false, timedOut: true } : execution("2");
  };
  if (mode === "stopped") f.pod.status = { phase: "Pending" };
  const result = await inspectKubernetesEnvironment(f.access, mode === "ambiguous" ? { ...instance, container: undefined } : instance, { timeoutMs: 5000 });
  expect(result.status).toBe("partial"); expect(result.nodeInfo).toEqual(nodeInfo); expect(result.podIpForward).toBeUndefined();
  expect(result.issues[0]?.source).toBe("pod.ip_forward");
});

test("missing Node fields remain gaps and a lone container can be resolved", async () => {
  const f = fixture(); f.pod.spec!.containers = [{ name: "runtime" }];
  f.node.status = { nodeInfo: { architecture: "arm64" } };
  f.access.exec = async () => execution("1");
  const result = await inspectKubernetesEnvironment(f.access, { ...instance, container: undefined }, { timeoutMs: 5000 });
  expect(result).toMatchObject({ status: "partial", podIpForward: 1, container: "runtime", nodeInfo: { architecture: "arm64" } });
  expect(result.issues.map(issue => issue.source)).toEqual(["node.operatingSystem", "node.osImage", "node.kernelVersion"]);
});

for (const mode of ["replaced", "missing", "forbidden"]) test(`unconfirmed Pod incarnation prevents exec and Node reads: ${mode}`, async () => {
  const f = fixture();
  if (mode === "forbidden") f.access.resources.get = async () => { throw new KubernetesError("denied", { kind: "permission_denied" }); };
  else f.pod.metadata!.uid = mode === "replaced" ? "new-uid" : undefined;
  const result = await inspectKubernetesEnvironment(f.access, instance, { timeoutMs: 5000 });
  expect(result.status).toBe("unavailable"); expect(result.issues[0]?.source).toBe("pod");
  expect(f.calls).toEqual([]); expect(f.queries.every(query => query.resource === "pods")).toBe(true);
});

test("missing nodeName and failed exec retain unavailable instead of invented defaults", async () => {
  const f = fixture(); f.pod.spec!.nodeName = undefined;
  f.access.exec = async () => { throw new Error("sensitive stderr"); };
  const result = await inspectKubernetesEnvironment(f.access, instance, { timeoutMs: 5000 });
  expect(result.status).toBe("unavailable"); expect(result.podIpForward).toBeUndefined();
  expect(result.nodeInfo).toEqual({}); expect(JSON.stringify(result)).not.toContain("sensitive");
});

test("cancellation propagates instead of becoming partial and invalid budgets do no I/O", async () => {
  const f = fixture(), abort = new AbortController();
  f.access.exec = async () => { abort.abort(new Error("stop")); throw abort.signal.reason; };
  await expect(inspectKubernetesEnvironment(f.access, instance, { timeoutMs: 5000, signal: abort.signal })).rejects.toThrow("stop");
  const clean = fixture();
  await expect(inspectKubernetesEnvironment(clean.access, instance, { timeoutMs: 0 })).rejects.toThrow("positive timeout");
  expect(clean.queries).toEqual([]);
});
