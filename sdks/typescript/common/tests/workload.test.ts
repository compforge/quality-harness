import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { sameWorkloadInstance, type Workload, type WorkloadInstance } from "../src/index.js";

const fixture = JSON.parse(readFileSync(
  new URL("../../../../conformance/workloads.json", import.meta.url), "utf8",
)) as {
  workloads: Workload[];
  instances: WorkloadInstance[];
  identity_changes: { patch: Partial<WorkloadInstance>; same: boolean }[];
};

test("shared declaration variants keep logical and physical names separate", () => {
  expect(fixture.workloads.map(value => value.location.kind)).toEqual(["resource", "service", "labels"]);
  const agent = fixture.workloads[0];
  expect(agent.name).toBe("agent");
  if (agent.location.kind !== "resource") throw new Error("expected resource fixture");
  expect(agent.location.name).toBe("hibot-agent");
  expect(fixture.workloads[2].namespace).toBeUndefined();
  expect(fixture.instances.map(value => value.workload)).toEqual(["agent", "agent"]);
});

test("shared instance identity separates environment and incarnation, not container or provenance", () => {
  const instance = fixture.instances[0];
  expect(sameWorkloadInstance(instance, fixture.instances[1])).toBe(false);
  for (const { patch, same } of fixture.identity_changes) {
    expect(sameWorkloadInstance(instance, { ...instance, ...patch })).toBe(same);
  }
});

test("location variants require their own fields at compile time", () => {
  const workload: Workload = {
    name: "api", platform: "kubernetes",
    location: { kind: "resource", resource_kind: "Deployment", name: "physical-api" },
  };
  // @ts-expect-error A network Service is a lookup path, not a workload controller.
  const invalid: Workload["location"] = { kind: "resource", resource_kind: "Service", name: "api" };
  // @ts-expect-error Labels are required for the labels variant.
  const missing: Workload["location"] = { kind: "labels" };
  expect(workload.location.kind).toBe("resource");
  void invalid;
  void missing;
});
