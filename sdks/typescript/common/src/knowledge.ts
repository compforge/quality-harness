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
  return `${heading(concept.name, 1)}\n${concept.description}`;
}

export function sectionToMarkdown(section: Section): string {
  return `${heading(section.title, 2)}\n${section.body}`;
}

export function articleToMarkdown(article: Article): string {
  requireIds(`article '${article.id}'`, article.sections);
  return paragraphs(`${heading(article.title, 1)}\n${article.summary}`, ...article.sections.map(sectionToMarkdown));
}

function workflowMarkdown(workflow: Workflow, level: number): string {
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
      const body = step.description.split("\n").map(line => childIndent + line).join("\n");
      // Keep a block boundary before authored Markdown (including indented code and thematic breaks).
      return [paragraphs(`${indent}${marker}**${step.title}**`, body), render(step.id, childIndent)]
        .filter(part => part.length > 0).join("\n");
    }).join("\n");
  };
  return paragraphs(`${heading(workflow.title, level)}\n${workflow.summary}`, render(undefined, ""));
}

export function stepToMarkdown(step: Step): string {
  return `${heading(step.title, 1)}\n${step.description}`;
}

export function workflowToMarkdown(workflow: Workflow): string {
  return workflowMarkdown(workflow, 1);
}

export function entryToMarkdown(entry: Entry): string {
  return paragraphs(`${heading(entry.name, 2)}\n${entry.description}`,
    entry.aliases?.length ? `Aliases: ${entry.aliases.join(", ")}` : undefined);
}

/** Domain renderers can include fields beyond the common entry reading contract. */
export function dictionaryToMarkdown<T extends Entry>(dictionary: Dictionary<T>,
  renderEntry: (entry: T) => string = entryToMarkdown): string {
  requireIds(`dictionary '${dictionary.id}'`, dictionary.entries);
  const source = dictionary.source;
  return paragraphs(`${heading(dictionary.title, 1)}\n${dictionary.summary}`,
    source ? `Source: ${source.name}@${source.version}${source.reference ? `\n\n${source.reference}` : ""}` : undefined,
    ...dictionary.entries.map(entry => renderEntry(entry)));
}

export interface KnowledgeMarkdownOptions {
  /** Consumers own domain-specific entry formatting; omitted renderers use name/description/aliases. */
  readonly renderEntry?: (dictionary: Dictionary, entry: Entry) => string;
}

function articleMarkdown(article: Article, level: number): string {
  return paragraphs(`${heading(article.title, level)}\n${article.summary}`,
    ...article.sections.map(section => `${heading(section.title, level + 1)}\n${section.body}`));
}

/** Deterministic Markdown projection. It performs no I/O and does not mutate declarations. */
export function knowledgeToMarkdown(knowledge: Knowledge, options: KnowledgeMarkdownOptions = {}): string {
  validateKnowledge(knowledge);
  const concepts = knowledge.concepts ?? [];
  const workflows = knowledge.workflows ?? [];
  const articles = knowledge.articles ?? [];
  const dictionaries = knowledge.dictionaries ?? [];
  return paragraphs(
    concepts.length ? paragraphs("## Concepts", ...concepts.map(concept =>
      `${heading(concept.name, 3)}\n${concept.description}`)) : undefined,
    workflows.length ? paragraphs("## Workflows", ...workflows.map(workflow => workflowMarkdown(workflow, 3))) : undefined,
    articles.length ? paragraphs("## Articles", ...articles.map(article => articleMarkdown(article, 3))) : undefined,
    dictionaries.length ? paragraphs("## Dictionaries", ...dictionaries.map(dictionary => {
      const source = dictionary.source;
      return paragraphs(`${heading(dictionary.title, 3)}\n${dictionary.summary}`,
        source ? `Source: ${source.name}@${source.version}${source.reference ? `\n\n${source.reference}` : ""}` : undefined,
        ...dictionary.entries.map(entry => options.renderEntry
          ? options.renderEntry(dictionary, entry)
          : paragraphs(`${heading(entry.name, 4)}\n${entry.description}`,
            entry.aliases?.length ? `Aliases: ${entry.aliases.join(", ")}` : undefined)));
    })) : undefined,
  );
}
