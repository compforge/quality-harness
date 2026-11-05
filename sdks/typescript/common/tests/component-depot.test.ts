import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { repositoryId, type Bin, type Component, type Depot, type RepositorySnapshot, type Tool } from "../src/index.js";

type ComponentValue = Omit<Component, "repository"> & { repository: RepositorySnapshot };
const cases = JSON.parse(readFileSync(
  new URL("../../../../conformance/common/component-depots.json", import.meta.url), "utf8",
)) as { case: string; component: ComponentValue }[];

// @case Optional tools/depot preserve legacy identity fields and shared checkout-relative declarations.
test("Component tools and Depot follow the shared declaration contract", () => {
  const components = cases.map(({ component }): Component => ({
    ...component,
    repository: { ...component.repository, id() { return repositoryId(this); } },
  }));
  for (const [index, component] of components.entries()) {
    expect(JSON.parse(JSON.stringify(component))).toEqual(cases[index].component);
  }
  const [bare, node, python, shared, empty] = components;
  expect(bare.tools).toBeUndefined();
  expect(bare.depot).toBeUndefined();
  expect(node.repository.id()).toBe(bare.repository.id());
  expect(node.name).toBe(bare.name);
  expect(empty.tools).toEqual([]);
  expect(empty.depot).toEqual({ bins: [] });
  const tools: readonly Tool[] = node.tools!;
  const depot: Depot = node.depot!;
  const bins: readonly Bin[] = depot.bins;
  expect(tools.map(tool => tool.name)).toEqual(["pnpm", "vite"]);
  expect(tools[0].version).toBe(">=9 <10");
  expect(tools[1].version).toBeUndefined();
  expect(bins.map(bin => bin.kind)).toEqual(["dependencies", "output", "cache"]);
  expect(shared.name).not.toBe(node.name);
  expect(shared.depot!.bins[0]).toEqual(bins[0]);
  expect(python.depot!.bins[0].path).toBe(".venv");
});
