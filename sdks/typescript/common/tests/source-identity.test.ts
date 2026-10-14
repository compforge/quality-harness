import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { conceptToMarkdown, knowledgeToMarkdown, stepToMarkdown, workflowToMarkdown, componentEcosystem, type Component } from "../src/index.js";

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
    markdown() { return knowledgeToMarkdown(this); },
    concepts: [{ id: "message", name: "Message", description: "A persisted conversation turn.", markdown() { return conceptToMarkdown(this); } }],
    workflows: [{ markdown() { return workflowToMarkdown(this); }, id: "reply", title: "Reply", summary: "Accept and answer a message.", steps: [
      { id: "accept", title: "Accept", description: "Store the incoming message.", markdown() { return stepToMarkdown(this); } },
      { id: "answer", title: "Answer", description: "Execute and persist the reply.", markdown() { return stepToMarkdown(this); } },
    ] }],
  };
  const product: import("../src/index.js").Product = {
    name: bare.name, description: described.description, knowledge,
  };
  expect(product.name).toBe(bare.name);
  expect(bare.description).toBeUndefined();
  expect(product.description).toBeTypeOf("string");
  expect(JSON.parse(JSON.stringify(product))).toEqual({
    name: bare.name, description: described.description, knowledge: JSON.parse(JSON.stringify(knowledge)),
  });
  // Type-level check: legacy fields cannot reappear on Product or Service.
  type LegacyFields = Extract<keyof import("../src/index.js").Product | keyof import("../src/index.js").Service,
    "concepts" | "workflows">;
  const knowledgeOnly: [LegacyFields] extends [never] ? true : false = true;
  expect(knowledgeOnly).toBe(true);
});


test("Repository preserves the direct fork parent without changing its identity fields", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../../conformance/common/source-identities.json", import.meta.url), "utf8",
  )) as { repositories: { forge: { name: string }; path: string; forked_from?: string }[] };
  const value = fixture.repositories[3];
  const fork: import("../src/index.js").Repository = {
    forge: value.forge, path: value.path, forkedFrom: value.forked_from,
  };
  const bare: import("../src/index.js").Repository = { forge: fork.forge, path: fork.path };
  const empty: import("../src/index.js").Repository = { ...bare, forkedFrom: null };
  expect(fork.forkedFrom).toBe(fixture.repositories[0].path);
  const parent: import("../src/index.js").Repository = { forge: fork.forge, path: fork.forkedFrom! };
  expect(parent).toEqual(fixture.repositories[0]);
  expect(bare.forkedFrom).toBeUndefined();
  expect(empty.forkedFrom).toBeNull();
  expect(fork.forge).toEqual(bare.forge);
  expect(fork.path).toBe(bare.path);
  expect(JSON.parse(JSON.stringify(fork)).forkedFrom).toEqual(value.forked_from);
  expect(JSON.parse(JSON.stringify(bare))).not.toHaveProperty("forkedFrom");
});
