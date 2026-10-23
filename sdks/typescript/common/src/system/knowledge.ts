/**
 * @spec Knowledge objects provide an I/O-free Markdown representation of their content.
 * @rule JSON preserves content only; consumers restore methods when loading declarations.
 */
export interface Markdown {
  markdown(): string;
}

/** A named domain object and its relationships or constraints. */
export interface Concept extends Markdown {
  readonly id: string;
  readonly name: string;
  readonly description: string;
}

/** A documented business process; steps are descriptions, not executable actions. */
export interface Workflow extends Markdown {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly steps: readonly Step[];
}

export interface Step extends Markdown {
  readonly id: string;
  /** Inclusion within this Workflow. Array order determines sibling order. */
  readonly parentId?: string;
  readonly title: string;
  readonly description: string;
}

/** A self-contained explanation of a topic. */
export interface Article extends Markdown {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly sections: readonly Section[];
}

export interface Section extends Markdown {
  readonly id: string;
  readonly title: string;
  /** Markdown authored by the knowledge owner. */
  readonly body: string;
}

/** Common reading identity; consumers extend entries with their typed domain fields. */
export interface Entry extends Markdown {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly aliases?: readonly string[];
}

export interface KnowledgeSource {
  readonly name: string;
  /** Definition version, independently of a consumer's distribution version. */
  readonly version: string;
  readonly reference?: string;
}

/** Entries retain domain fields. Names and codes need not be unique; IDs must be. */
export interface Dictionary<T extends Entry = Entry> extends Markdown {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly source?: KnowledgeSource;
  readonly entries: readonly T[];
}

/**
 * @spec Knowledge is owner-scoped offline content, independent of runtime evidence and identity.
 * @rule Top-level IDs are unique per kind; child IDs are unique within their containing object.
 * @see {@link component://docs/kernel.md}
 */
export interface Knowledge extends Markdown {
  readonly concepts?: readonly Concept[];
  readonly workflows?: readonly Workflow[];
  readonly articles?: readonly Article[];
  readonly dictionaries?: readonly Dictionary[];
}

function requireIds(kind: string, values: readonly { readonly id: string }[]): void {
  const ids = new Set<string>();
  for (const value of values) {
    if (!value.id.trim()) throw new Error(`${kind}: ID must not be empty`);
    if (ids.has(value.id)) throw new Error(`${kind}: duplicate ID '${value.id}'`);
    ids.add(value.id);
  }
}

/**
 * @spec Parent IDs express inclusion only; missing parents, self-reference and cycles are invalid.
 * @case id=workflow_hierarchy,desc=`Parent appears after child`,expect=`Sibling array order is preserved`
 */
export function validateWorkflow(workflow: Workflow): void {
  const label = `workflow '${workflow.id}'`;
  requireIds(label, workflow.steps);
  const steps = new Map(workflow.steps.map(step => [step.id, step]));
  for (const step of workflow.steps) {
    if (step.parentId !== undefined && !steps.has(step.parentId)) {
      throw new Error(`${label}: step '${step.id}' has unknown parent '${step.parentId}'`);
    }
  }
  const visited = new Set<string>();
  for (const step of workflow.steps) {
    const path = new Set<string>();
    let current: Step | undefined = step;
    while (current && !visited.has(current.id)) {
      if (path.has(current.id)) throw new Error(`${label}: parent cycle at step '${current.id}'`);
      path.add(current.id);
      current = current.parentId === undefined ? undefined : steps.get(current.parentId);
    }
    for (const id of path) visited.add(id);
  }
}

/** Validate declaration identities before exposing references or rendering any content. */
export function validateKnowledge(knowledge: Knowledge): void {
  requireIds("concept", knowledge.concepts ?? []);
  requireIds("workflow", knowledge.workflows ?? []);
  requireIds("article", knowledge.articles ?? []);
  requireIds("dictionary", knowledge.dictionaries ?? []);
  for (const workflow of knowledge.workflows ?? []) validateWorkflow(workflow);
  for (const article of knowledge.articles ?? []) requireIds(`article '${article.id}'`, article.sections);
  for (const dictionary of knowledge.dictionaries ?? []) requireIds(`dictionary '${dictionary.id}'`, dictionary.entries);
}

function heading(title: string, level: number): string {
  return `${"#".repeat(level)} ${title}`;
}

function paragraphs(...parts: readonly (string | undefined)[]): string {
  return parts.filter(part => part !== undefined && part.length > 0).join("\n\n");
}

export function conceptToMarkdown(concept: Concept): string {
  return `${heading(concept.name, 2)}\n${concept.description}`;
}

export function sectionToMarkdown(section: Section): string {
  return `${heading(section.title, 3)}\n${section.body}`;
}

export function articleToMarkdown(article: Article): string {
  requireIds(`article '${article.id}'`, article.sections);
  return paragraphs(`${heading(article.title, 2)}\n${article.summary}`, ...article.sections.map(section => section.markdown()));
}

export function workflowToMarkdown(workflow: Workflow): string {
  validateWorkflow(workflow);
  const children = new Map<string | undefined, Step[]>();
  for (const step of workflow.steps) {
    const siblings = children.get(step.parentId) ?? [];
    siblings.push(step);
    children.set(step.parentId, siblings);
  }
  const render = (parentId: string | undefined, indent: string): string => {
    return (children.get(parentId) ?? []).map((step, index) => {
      const marker = `${index + 1}. `;
      const childIndent = indent + " ".repeat(marker.length);
      const body = step.markdown().split("\n").map((line, lineIndex) =>
        lineIndex === 0 ? `${indent}${marker}${line}` : `${childIndent}${line}`).join("\n");
      return [body, render(step.id, childIndent)]
        .filter(part => part.length > 0).join("\n");
    }).join("\n");
  };
  return paragraphs(`${heading(workflow.title, 2)}\n${workflow.summary}`, render(undefined, ""));
}

export function stepToMarkdown(step: Step): string {
  // Preserve a block boundary before authored descriptions such as indented code and thematic breaks.
  return paragraphs(`**${step.title}**`, step.description);
}

export function entryToMarkdown(entry: Entry): string {
  return paragraphs(`${heading(entry.name, 3)}\n${entry.description}`,
    entry.aliases?.length ? `Aliases: ${entry.aliases.join(", ")}` : undefined);
}

/** Entries own their domain-specific output; the dictionary owns its title, source and order. */
export function dictionaryToMarkdown<T extends Entry>(dictionary: Dictionary<T>): string {
  requireIds(`dictionary '${dictionary.id}'`, dictionary.entries);
  const source = dictionary.source;
  return paragraphs(`${heading(dictionary.title, 2)}\n${dictionary.summary}`,
    source ? `Source: ${source.name}@${source.version}${source.reference ? `\n\n${source.reference}` : ""}` : undefined,
    ...dictionary.entries.map(entry => entry.markdown()));
}

/**
 * @spec Parents compose child markdown() output; only the child owns its content rendering.
 * @rule Helpers never invoke the receiver's own markdown(), so declarations can safely delegate to them.
 * @case id=knowledge_composition,desc=`Nested objects customize Markdown`,expect=`Root output preserves each child result`
 */
export function knowledgeToMarkdown(knowledge: Knowledge): string {
  validateKnowledge(knowledge);
  const concepts = knowledge.concepts ?? [];
  const workflows = knowledge.workflows ?? [];
  const articles = knowledge.articles ?? [];
  const dictionaries = knowledge.dictionaries ?? [];
  return paragraphs(
    concepts.length ? paragraphs("# Concepts", ...concepts.map(concept => concept.markdown())) : undefined,
    workflows.length ? paragraphs("# Workflows", ...workflows.map(workflow => workflow.markdown())) : undefined,
    articles.length ? paragraphs("# Articles", ...articles.map(article => article.markdown())) : undefined,
    dictionaries.length ? paragraphs("# Dictionaries", ...dictionaries.map(dictionary => dictionary.markdown())) : undefined,
  );
}
