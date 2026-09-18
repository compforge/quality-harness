import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { componentEcosystem, type Component } from "../src/index.js";

test("Component metadata follows the shared optional metadata contract", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../../conformance/common/source-identities.json", import.meta.url), "utf8",
  )) as { components: Component[] };
  const [first, , unspecified] = fixture.components;
  expect(first.language).toBe("go");
  expect(componentEcosystem(first)).toBe("go");
  expect(unspecified.language).toBeUndefined();
  expect(componentEcosystem(unspecified)).toBeUndefined();
  const relabeled: Component = { ...first, language: "typescript" };
  expect(relabeled.name).toBe(first.name);
  expect(relabeled.repository).toEqual(first.repository);
  expect(JSON.parse(JSON.stringify(relabeled)).language).toBe("typescript");
  expect(componentEcosystem(relabeled)).toBe("node");
  expect(JSON.parse(JSON.stringify(relabeled))).not.toHaveProperty("ecosystem");
});


test("ecosystem is derived from language across the shared contract", () => {
  const cases = JSON.parse(readFileSync(
    new URL("../../../../conformance/common/component-ecosystems.json", import.meta.url), "utf8",
  )) as { language: string | null; ecosystem: string | null }[];
  for (const item of cases) {
    const component: Component = {
      repository: { forge: { name: "github" }, path: "example/api" },
      name: "api",
      ...(item.language === null ? {} : { language: item.language }),
    };
    expect(componentEcosystem(component)).toBe(item.ecosystem ?? undefined);
  }
});
