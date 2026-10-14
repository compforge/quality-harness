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

test("Product shares the neutral business identity contract", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../../conformance/common/source-identities.json", import.meta.url), "utf8",
  )) as { products: import("../src/index.js").Product[] };
  const [bare, described] = fixture.products;
  expect(bare).toEqual({ name: "example-product" });
  const knowledge: import("../src/index.js").Knowledge = {
    concepts: [{ id: "message", name: "Message", description: "A persisted conversation turn." }],
    workflows: [{ id: "reply", title: "Reply", summary: "Accept and answer a message.", steps: [
      { id: "accept", title: "Accept", description: "Store the incoming message." },
      { id: "answer", title: "Answer", description: "Execute and persist the reply." },
    ] }],
  };
  const product: import("../src/index.js").Product = {
    name: bare.name, description: described.description, knowledge,
  };
  expect(product.name).toBe(bare.name);
  expect(bare.description).toBeUndefined();
  expect(product.description).toBeTypeOf("string");
  expect(JSON.parse(JSON.stringify(product))).toEqual({
    name: bare.name, description: described.description, knowledge,
  });
  // Type-level check: legacy fields cannot reappear on Product or Service.
  type LegacyFields = Extract<keyof import("../src/index.js").Product | keyof import("../src/index.js").Service,
    "concepts" | "workflows">;
  const knowledgeOnly: [LegacyFields] extends [never] ? true : false = true;
  expect(knowledgeOnly).toBe(true);
});
