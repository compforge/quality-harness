import { expect, test } from "bun:test";
import {
  articleToMarkdown, conceptToMarkdown, dictionaryToMarkdown, knowledgeToMarkdown,
  validateKnowledge, validateWorkflow, workflowToMarkdown,
  type Dictionary, type Entry, type Knowledge, type Product, type Workflow,
} from "../src/index.js";

const workflow: Workflow = {
  id: "start", title: "Start", summary: "Place and initialize a sandbox.",
  steps: [
    { id: "restore", parentId: "prepare", title: "Restore", description: "Restore files." },
    { id: "prepare", title: "Prepare", description: "Create runtime." },
    { id: "initialize", parentId: "prepare", title: "Initialize", description: "Configure runtime." },
    { id: "ready", title: "Ready", description: "Check access." },
  ],
};

const knowledge: Knowledge = {
  concepts: [{ id: "carrier", name: "Carrier", description: "Infrastructure capacity." }],
  workflows: [workflow],
  articles: [{ id: "workspace", title: "Workspace", summary: "Persistence rules.", sections: [
    { id: "restore", title: "Restore", body: "Files survive a stop.\n\n```sh\nrestore --workspace one\n```" },
  ] }],
};

test("knowledge survives JSON and composes with Product without changing identity", () => {
  const product: Product = { name: "app", knowledge };
  const restored: Product = JSON.parse(JSON.stringify(product));
  validateKnowledge(restored.knowledge!);
  expect(restored.name).toBe("app");
  expect(restored.knowledge).toEqual(knowledge);
  const before = JSON.stringify(restored);
  expect(knowledgeToMarkdown(restored.knowledge!)).toBe(knowledgeToMarkdown(knowledge));
  expect(JSON.stringify(restored)).toBe(before);
});

test("workflow parent inclusion supports forward references and preserves sibling order", () => {
  const text = workflowToMarkdown(workflow);
  expect(text).toContain("1. **Prepare**\n\n   Create runtime.\n\n   1. **Restore**");
  expect(text).toContain("   2. **Initialize**");
  expect(text).toContain("2. **Ready**");
  expect(text.indexOf("Restore")).toBeLessThan(text.indexOf("Initialize"));
});

test("invalid parent graphs fail before rendering", () => {
  const cases = [
    [{ id: "one", parentId: "missing", title: "One", description: "" }],
    [{ id: "one", parentId: "one", title: "One", description: "" }],
    [{ id: "one", parentId: "two", title: "One", description: "" },
      { id: "two", parentId: "one", title: "Two", description: "" }],
  ];
  for (const steps of cases) {
    expect(() => validateWorkflow({ ...workflow, steps })).toThrow();
    expect(() => workflowToMarkdown({ ...workflow, steps })).toThrow();
  }
});

test("IDs are scoped by kind and container; duplicate and empty references fail", () => {
  validateKnowledge({ concepts: [{ id: "same", name: "One", description: "" }], articles: [
    { id: "same", title: "One", summary: "", sections: [{ id: "same", title: "One", body: "" }] },
    { id: "other", title: "Two", summary: "", sections: [{ id: "same", title: "One", body: "" }] },
  ] });
  expect(() => validateKnowledge({ concepts: [...knowledge.concepts!, ...knowledge.concepts!] })).toThrow("duplicate ID");
  expect(() => articleToMarkdown({ ...knowledge.articles![0]!, sections: [
    ...knowledge.articles![0]!.sections, ...knowledge.articles![0]!.sections,
  ] })).toThrow("duplicate ID");
  expect(() => validateKnowledge({ concepts: [{ id: " ", name: "One", description: "" }] })).toThrow("empty");
});

test("dictionary retains typed fields, ambiguous codes and source independently of distribution", () => {
  interface ErrorEntry extends Entry { readonly code: string; readonly defaultHttpStatus: number }
  const dictionary: Dictionary<ErrorEntry> = {
    id: "errors", title: "Errors", summary: "Published defaults.",
    source: { name: "api", version: "1.2.3", reference: "https://example.com/errors" },
    entries: [
      { id: "bad-request", code: "001", name: "BadRequest", description: "Invalid request.", defaultHttpStatus: 400 },
      { id: "format-error", code: "001", name: "FormatError", description: "Invalid format.", aliases: ["InvalidInput"], defaultHttpStatus: 400 },
    ],
  };
  validateKnowledge({ dictionaries: [dictionary] });
  const before = JSON.stringify(dictionary);
  const render = (entry: ErrorEntry) => `## ${entry.code} · ${entry.name}\n\n${entry.description}\n\nHTTP ${entry.defaultHttpStatus}`;
  expect(dictionaryToMarkdown(dictionary, render)).toContain("001 · BadRequest");
  expect(dictionaryToMarkdown(dictionary, render)).toContain("001 · FormatError");
  expect(dictionaryToMarkdown(dictionary, render)).toContain("Source: api@1.2.3");
  expect(dictionaryToMarkdown(dictionary)).toContain("Aliases: InvalidInput");
  expect(JSON.stringify(dictionary)).toBe(before);
  expect(() => dictionaryToMarkdown({ ...dictionary, entries: [dictionary.entries[0]!, dictionary.entries[0]!] })).toThrow("duplicate ID");
});

test("Markdown preserves authored bodies and omits absent categories", () => {
  expect(knowledgeToMarkdown({})).toBe("");
  expect(conceptToMarkdown(knowledge.concepts![0]!)).toBe("# Carrier\n\nInfrastructure capacity.");
  const article = knowledge.articles![0]!;
  expect(articleToMarkdown(article)).toContain(article.sections[0]!.body);
  const text = knowledgeToMarkdown(knowledge);
  expect(text).toContain("## Concepts\n\n### Carrier");
  expect(text).toContain("## Articles\n\n### Workspace\n\nPersistence rules.\n\n#### Restore");
  expect(text).not.toContain("## Dictionaries");
});

test("nested Markdown accounts for ordered-list markers with multiple digits", () => {
  const steps = Array.from({ length: 10 }, (_, index) => ({
    id: `step-${index}`, title: `Step ${index}`, description: "Details.",
  }));
  const text = workflowToMarkdown({ ...workflow, steps: [
    ...steps, { id: "nested", parentId: "step-9", title: "Nested", description: "Child details." },
  ] });
  expect(text).toContain("10. **Step 9**\n\n    Details.\n\n    1. **Nested**\n\n       Child details.");
});
