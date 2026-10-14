import { expect, test } from "bun:test";
import {
  articleToMarkdown, conceptToMarkdown, dictionaryToMarkdown, knowledgeToMarkdown,
  validateKnowledge, validateWorkflow, workflowToMarkdown, stepToMarkdown, sectionToMarkdown, entryToMarkdown,
  type Article, type Concept, type Dictionary, type Entry, type Knowledge, type Markdown, type Product, type Workflow,
} from "../src/index.js";

const workflow: Workflow = {
  markdown() { return workflowToMarkdown(this); },
  id: "start", title: "Start", summary: "Place and initialize a sandbox.",
  steps: [
    { id: "restore", parentId: "prepare", title: "Restore", description: "Restore files.", markdown() { return stepToMarkdown(this); } },
    { id: "prepare", title: "Prepare", description: "Create runtime.", markdown() { return stepToMarkdown(this); } },
    { id: "initialize", parentId: "prepare", title: "Initialize", description: "Configure runtime.", markdown() { return stepToMarkdown(this); } },
    { id: "ready", title: "Ready", description: "Check access.", markdown() { return stepToMarkdown(this); } },
  ],
};

const knowledge: Knowledge = {
  markdown() { return knowledgeToMarkdown(this); },
  concepts: [{ id: "carrier", name: "Carrier", description: "Infrastructure capacity.", markdown() { return conceptToMarkdown(this); } }],
  workflows: [workflow],
  articles: [{ markdown() { return articleToMarkdown(this); }, id: "workspace", title: "Workspace", summary: "Persistence rules.", sections: [
    { id: "restore", title: "Restore", body: "Files survive a stop.\n\n```sh\nrestore --workspace one\n```", markdown() { return sectionToMarkdown(this); } },
  ] }],
};

test("JSON preserves Knowledge content; consumers restore methods before using the contract", () => {
  const product: Product = { name: "app", knowledge };
  const restored = JSON.parse(JSON.stringify(product));
  validateKnowledge(restored.knowledge!);
  expect(restored.name).toBe("app");
  expect(restored.knowledge.markdown).toBeUndefined();
  expect(restored.knowledge.concepts[0].markdown).toBeUndefined();
  const concept: Concept = {
    ...restored.knowledge.concepts[0],
    markdown() { return conceptToMarkdown(this); },
  };
  expect(concept.markdown()).toBe(knowledge.concepts![0]!.markdown());
  expect(JSON.stringify(concept)).toBe(JSON.stringify(restored.knowledge.concepts[0]));
  const before = JSON.stringify(restored);
  expect(() => knowledgeToMarkdown(restored.knowledge)).toThrow("markdown");
  expect(JSON.stringify(restored)).toBe(before);
});

test("workflow parent inclusion supports forward references and preserves sibling order", () => {
  const text = workflowToMarkdown(workflow);
  expect(text).toContain("1. **Prepare**\n   \n   Create runtime.\n   1. **Restore**");
  expect(text).toContain("   2. **Initialize**");
  expect(text).toContain("2. **Ready**");
  expect(text.indexOf("Restore")).toBeLessThan(text.indexOf("Initialize"));
});

test("invalid parent graphs fail before rendering", () => {
  const cases = [
    [{ id: "one", parentId: "missing", title: "One", description: "", markdown() { return stepToMarkdown(this); } }],
    [{ id: "one", parentId: "one", title: "One", description: "", markdown() { return stepToMarkdown(this); } }],
    [{ id: "one", parentId: "two", title: "One", description: "", markdown() { return stepToMarkdown(this); } },
      { id: "two", parentId: "one", title: "Two", description: "", markdown() { return stepToMarkdown(this); } }],
  ];
  for (const steps of cases) {
    expect(() => validateWorkflow({ ...workflow, steps })).toThrow();
    expect(() => workflowToMarkdown({ ...workflow, steps })).toThrow();
  }
});

test("IDs are scoped by kind and container; duplicate and empty references fail", () => {
  validateKnowledge({ markdown() { return knowledgeToMarkdown(this); }, concepts: [{ id: "same", name: "One", description: "", markdown() { return conceptToMarkdown(this); } }], articles: [
    { markdown() { return articleToMarkdown(this); }, id: "same", title: "One", summary: "", sections: [{ id: "same", title: "One", body: "", markdown() { return sectionToMarkdown(this); } }] },
    { markdown() { return articleToMarkdown(this); }, id: "other", title: "Two", summary: "", sections: [{ id: "same", title: "One", body: "", markdown() { return sectionToMarkdown(this); } }] },
  ] });
  expect(() => validateKnowledge({ markdown() { return knowledgeToMarkdown(this); }, concepts: [...knowledge.concepts!, ...knowledge.concepts!] })).toThrow("duplicate ID");
  expect(() => articleToMarkdown({ ...knowledge.articles![0]!, sections: [
    ...knowledge.articles![0]!.sections, ...knowledge.articles![0]!.sections,
  ] })).toThrow("duplicate ID");
  expect(() => validateKnowledge({ markdown() { return knowledgeToMarkdown(this); }, concepts: [{ id: " ", name: "One", description: "", markdown() { return conceptToMarkdown(this); } }] })).toThrow("empty");
});

test("dictionary retains typed fields, ambiguous codes and source independently of distribution", () => {
  interface ErrorEntry extends Entry { readonly code: string; readonly defaultHttpStatus: number }
  const render = (entry: ErrorEntry) => `${entryToMarkdown(entry)}\n\nCode: ${entry.code}; HTTP ${entry.defaultHttpStatus}`;
  const dictionary: Dictionary<ErrorEntry> = {
    markdown() { return dictionaryToMarkdown(this); },
    id: "errors", title: "Errors", summary: "Published defaults.",
    source: { name: "api", version: "1.2.3", reference: "https://example.com/errors" },
    entries: [
      { id: "bad-request", code: "001", name: "BadRequest", description: "Invalid request.", defaultHttpStatus: 400, markdown() { return render(this); } },
      { id: "format-error", code: "001", name: "FormatError", description: "Invalid format.", aliases: ["InvalidInput"], defaultHttpStatus: 400, markdown() { return render(this); } },
    ],
  };
  validateKnowledge({ markdown() { return knowledgeToMarkdown(this); }, dictionaries: [dictionary] });
  const before = JSON.stringify(dictionary);
  expect(dictionaryToMarkdown(dictionary)).toContain("### BadRequest");
  expect(dictionaryToMarkdown(dictionary)).toContain("### FormatError");
  expect(dictionaryToMarkdown(dictionary)).toContain("Source: api@1.2.3");
  expect(dictionary.markdown()).toContain("Code: 001; HTTP 400");
  expect(dictionaryToMarkdown(dictionary)).toContain("Aliases: InvalidInput");
  expect(JSON.stringify(dictionary)).toBe(before);
  expect(() => dictionaryToMarkdown({ ...dictionary, entries: [dictionary.entries[0]!, dictionary.entries[0]!] })).toThrow("duplicate ID");
});

test("Markdown preserves authored bodies and omits absent categories", () => {
  expect(knowledgeToMarkdown({ markdown() { return knowledgeToMarkdown(this); } })).toBe("");
  expect(conceptToMarkdown(knowledge.concepts![0]!)).toBe("## Carrier\nInfrastructure capacity.");
  const article = knowledge.articles![0]!;
  expect(articleToMarkdown(article)).toContain(article.sections[0]!.body);
  const text = knowledgeToMarkdown(knowledge);
  expect(text).toContain("# Concepts\n\n## Carrier");
  expect(text).toContain("# Articles\n\n## Workspace\nPersistence rules.\n\n### Restore");
  expect(text).not.toContain("# Dictionaries");
});

test("nested Markdown accounts for ordered-list markers with multiple digits", () => {
  const steps = Array.from({ length: 10 }, (_, index) => ({
    id: `step-${index}`, title: `Step ${index}`, description: "Details.",
    markdown() { return stepToMarkdown(this); },
  }));
  const text = workflowToMarkdown({ ...workflow, steps: [
    ...steps, { id: "nested", parentId: "step-9", title: "Nested", description: "Child details.", markdown() { return stepToMarkdown(this); } },
  ] });
  expect(text).toContain("10. **Step 9**\n    \n    Details.\n    1. **Nested**\n       \n       Child details.");
});


test("all content types expose the same Markdown method contract", () => {
  const entry: Entry = { id: "one", name: "One", description: "A definition.",
    markdown() { return entryToMarkdown(this); } };
  const dictionary: Dictionary = { id: "terms", title: "Terms", summary: "Definitions.", entries: [entry],
    markdown() { return dictionaryToMarkdown(this); } };
  const values: readonly Markdown[] = [knowledge, knowledge.concepts![0]!, workflow, workflow.steps[0]!,
    knowledge.articles![0]!, knowledge.articles![0]!.sections[0]!, dictionary, entry];
  for (const value of values) expect(value.markdown().length).toBeGreaterThan(0);
  // @ts-expect-error Content alone is not the callable Concept contract.
  const missingMethod: Concept = { id: "one", name: "One", description: "A definition." };
  void missingMethod;
});

test("compact steps preserve authored paragraphs and fenced blocks", () => {
  const description = "First paragraph.\n\nSecond paragraph.\n\n```sh\necho one\n\necho two\n```";
  const value: Workflow = { ...workflow, steps: [
    { id: "one", title: "One", description, markdown() { return stepToMarkdown(this); } },
    { id: "two", title: "Two", description: "Next step.", markdown() { return stepToMarkdown(this); } },
  ] };
  expect(value.markdown()).toContain(`1. **One**\n   \n${description.split("\n").map(line => `   ${line}`).join("\n")}\n2. **Two**\n   \n   Next step.`);
});


test("step descriptions keep a Markdown block boundary", () => {
  const value: Workflow = { ...workflow, steps: [
    { id: "code", title: "Code", description: "    echo one", markdown() { return stepToMarkdown(this); } },
    { id: "rule", title: "Rule", description: "---", markdown() { return stepToMarkdown(this); } },
  ] };
  expect(value.markdown()).toContain("1. **Code**\n   \n       echo one\n2. **Rule**\n   \n   ---");
});


test("parents compose custom child Markdown through the entire knowledge tree", () => {
  const calls: string[] = [];
  const customWorkflow: Workflow = {
    ...workflow,
    markdown() { calls.push("workflow"); return workflowToMarkdown(this); },
    steps: [{ ...workflow.steps[0]!, parentId: undefined,
      markdown() { calls.push("step"); return "Custom step\n\n```md\n# Keep this heading\n```"; } }],
  };
  const customArticle: Article = {
    ...knowledge.articles![0]!,
    markdown() { calls.push("article"); return articleToMarkdown(this); },
    sections: [{ ...knowledge.articles![0]!.sections[0]!,
      markdown() { calls.push("section"); return "Custom section\n\n```md\n## Keep this too\n```"; } }],
  };
  const customDictionary: Dictionary = {
    id: "terms", title: "Terms", summary: "Definitions.",
    markdown() { calls.push("dictionary"); return dictionaryToMarkdown(this); },
    entries: [{ id: "one", name: "One", description: "Default entry.",
      markdown() { calls.push("entry"); return "Custom entry"; } }],
  };
  const value: Knowledge = {
    markdown() { return knowledgeToMarkdown(this); },
    concepts: [{ ...knowledge.concepts![0]!,
      markdown() { calls.push("concept"); return "Custom concept"; } }],
    workflows: [customWorkflow], articles: [customArticle], dictionaries: [customDictionary],
  };
  const text = value.markdown();
  expect(calls).toEqual(["concept", "workflow", "step", "article", "section", "dictionary", "entry"]);
  expect(text).toContain("# Concepts\n\nCustom concept");
  expect(text).toContain("1. Custom step\n   \n   ```md\n   # Keep this heading\n   ```");
  expect(text).toContain("Custom section\n\n```md\n## Keep this too\n```");
  expect(text).toContain("# Dictionaries\n\n## Terms\nDefinitions.\n\nCustom entry");
  expect(text).not.toContain("Infrastructure capacity.");
  expect(text).not.toContain("Restore files.");
  expect(text).not.toContain("Default entry.");
});
