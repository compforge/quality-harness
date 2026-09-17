import { expect, test } from "bun:test";
import { sameWorkloadInstance, type Workload, type WorkloadInstance } from "@compforge/harness-common";
import { readFileSync } from "node:fs";
import { KubernetesClientFactory } from "../src/kubernetes/client-factory";
import { KubernetesClient } from "../src/kubernetes/client";
import { resolveWorkload } from "../src/kubernetes/workload";
import type { Resource, ResourceAccess } from "../src/kubernetes/resources";
import { KubernetesError, ToolboxError, kubernetesHttpError } from "../src/errors";

const pod = (namespace = "ns", uid = "uid-1"): Resource =>
  ({ metadata: { name: "pod-1", uid, namespace } });

test("client resolves all three locations with namespace override and no readiness filter", async () => {
  const calls: unknown[] = [];
  const access: ResourceAccess = {
    async get(namespace, resource, name, selector) {
      calls.push([namespace, resource, name, selector]);
      if (resource === "pods") return name ? pod(namespace) : { items: [pod(namespace), {
        metadata: { name: "pod-2", namespace, uid: "uid-2" },
      }] };
      return { spec: { selector: resource === "services" ? { app: "api" }
        : { matchLabels: { app: "api" }, matchExpressions: [{ key: "tier", operator: "In", values: ["b", "a"] }] } } };
    },
  };
  const client = new KubernetesClient({ namespace: "ns" }, undefined, undefined, undefined, access);
  for (const location of [
    { kind: "resource", resource_kind: "Deployment", name: "api" },
    { kind: "service", name: "api" },
    { kind: "labels", labels: { app: "api" } },
  ] satisfies Workload["location"][]) {
    const result = await client.resolveWorkload({ platform: "kubernetes", name: "logical", namespace: "other", location, container: "app" }, "env");
    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({ workload: "logical", namespace: "other", uid: "uid-1", container: "app" });
  }
  expect(calls[1]).toEqual(["other", "pods", undefined, "app=api,tier in (a,b)"]);
  await client.dispose();
  expect(() => client.resolveWorkload({ platform: "kubernetes", name: "api", location: { kind: "labels", labels: { app: "api" } } }, "env")).toThrow("disposed");
});

test("empty inventory differs from missing resource and selectorless Service", async () => {
  const workload: Workload = { platform: "kubernetes", name: "api", location: { kind: "service", name: "api" } };
  const access: ResourceAccess = { get: async () => ({ spec: {} }) };
  await expect(resolveWorkload(access, workload, "ns", "env")).rejects.toMatchObject({ kind: "unsupported_operation" });
  access.get = async () => { throw kubernetesHttpError(404); };
  await expect(resolveWorkload(access, workload, "ns", "env")).rejects.toMatchObject({ kind: "resource_not_found" });
  access.get = async () => ({ items: [] });
  const labels: Workload = { ...workload, location: { kind: "labels", labels: { app: "api" } } };
  expect(await resolveWorkload(access, labels, "ns", "env")).toEqual([]);
  await expect(resolveWorkload(access, labels, "", "env")).rejects.toMatchObject({ kind: "invalid_argument" });
  await expect(resolveWorkload(access, labels, "ns", " ")).rejects.toMatchObject({ kind: "invalid_argument" });
  access.get = async () => ({ items: [pod("ns", "")] });
  await expect(resolveWorkload(access, labels, "ns", "env")).rejects.toMatchObject({ kind: "invalid_response" });
});

test("toolbox errors preserve safe kinds, native codes and causes", () => {
  for (const [status, kind] of [[401, "authentication_failed"], [403, "permission_denied"], [404, "resource_not_found"], [408, "timeout"], [429, "limit_exceeded"], [500, "operation_failed"]]) {
    expect(kubernetesHttpError(status as number)).toMatchObject({ code: status, kind });
  }
  const cause = new Error("private credentials");
  const error = new KubernetesError("Discovery failed", { kind: "operation_failed", cause });
  expect(error).toBeInstanceOf(ToolboxError);
  expect(error.cause).toBe(cause);
  expect(error.message).not.toContain("credentials");
});

test("shared target identity stays independent of connection keys and access aliases", async () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../../conformance/workloads.json", import.meta.url), "utf8",
  )) as {
    target_bindings: { environment: string; access: { kubeconfig: string; context: string } }[];
    instances: WorkloadInstance[];
  };
  const instances: WorkloadInstance[] = [];
  const keys: string[] = [];
  const limits = { timeoutMs: 1000, concurrency: 1, maxBytes: 1024 };
  for (const binding of fixture.target_bindings) {
    const options = { ...binding.access, namespace: "ns" };
    keys.push(new KubernetesClientFactory(options, limits).key);
    const client = new KubernetesClient(options, undefined, undefined, limits, {
      get: async () => ({ items: [pod()] }),
    });
    try {
      const result = await client.resolveWorkload({
        name: "api", platform: "kubernetes", location: { kind: "labels", labels: { app: "api" } },
      }, binding.environment);
      instances.push(result[0]);
    } finally { await client.dispose(); }
  }
  expect(keys[0]).not.toBe(keys[1]);
  expect(sameWorkloadInstance(instances[0], instances[1])).toBe(true);
  expect(sameWorkloadInstance(instances[0], instances[2])).toBe(false);
  expect(instances[0].environment).toBe(fixture.instances[0].environment);
});
