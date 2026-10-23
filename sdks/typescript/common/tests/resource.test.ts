import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { sameResourceInstance, type Resource, type ResourceInstanceRef, type ResourceSnapshot } from "../src/index.js";

const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/resources.json", import.meta.url), "utf8")) as {
  resource: Resource; instances: ResourceInstanceRef[]; snapshots: Omit<ResourceSnapshot, "resource">[];
};

test("shared resource identity is independent of managing/using Service and display metadata", () => {
  expect(sameResourceInstance(fixture.instances[0], fixture.instances[1])).toBe(true);
  expect(sameResourceInstance(fixture.instances[0], fixture.instances[2])).toBe(false);
  expect(sameResourceInstance(fixture.instances[0], fixture.instances[3])).toBe(false);
});

test("partial page may have an independent total; unavailable does not mean empty", () => {
  const page: ResourceSnapshot = { resource: fixture.resource, ...fixture.snapshots[0],
    instances: [{ ref: fixture.instances[0], state: "retained", details: { pod: "carrier-1" } }] };
  expect(page.instances).toHaveLength(1);
  expect(page.totalCount).toBe(17);
  expect(page.measurements?.[0].scope).toBe("observed");
  expect(page.nextCursor).toBe("next");
  const missing: ResourceSnapshot = { resource: fixture.resource, ...fixture.snapshots[1] };
  expect(missing.totalCount).toBeUndefined();
  expect(missing.instances).toBeUndefined();
  expect(JSON.parse(JSON.stringify(page))).toEqual(page);
});
