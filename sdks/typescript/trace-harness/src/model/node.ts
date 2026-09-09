export type Emphasis = "normal" | "dim" | "strong";
export type Severity = "info" | "warn" | "error";

export interface Field {
  label: string;
  value: string;
  emphasis?: Emphasis;
}

export interface Finding {
  ref?: string;
  source: string;
  severity: Severity;
  scope?: "node" | "trace" | "cohort";
  rank?: number;
  note?: string;
  data?: Record<string, unknown>;
  symptoms?: string[];
  causes?: string[];
}

export interface NodeInit {
  kind: string;
  name: string;
  primary_span_id: string;
  span_ids: string[];
  facts: Record<string, unknown>;
  start_ms: number;
  duration_ms: number;
  service?: string;
  node_id: string;
  parent_node_id?: string;
  error_span_ids?: string[];
  brief?: Field[];
  error_text?: string;
}

export class Node {
  readonly kind: string;
  readonly name: string;
  readonly primary_span_id: string;
  readonly span_ids: string[];
  readonly facts: Record<string, unknown>;
  readonly start_ms: number;
  readonly duration_ms: number;
  readonly service?: string;
  readonly node_id: string;
  parent_node_id?: string;
  readonly error_span_ids: string[];
  brief: Field[];
  error_text: string;

  constructor(init: NodeInit) {
    this.kind = init.kind;
    this.name = init.name;
    this.primary_span_id = init.primary_span_id;
    this.span_ids = init.span_ids;
    this.facts = init.facts;
    this.start_ms = init.start_ms;
    this.duration_ms = init.duration_ms;
    this.service = init.service;
    this.node_id = init.node_id;
    this.parent_node_id = init.parent_node_id;
    this.error_span_ids = init.error_span_ids ?? [];
    this.brief = init.brief ?? [];
    this.error_text = init.error_text ?? "";
  }

  get end_ms(): number {
    return this.start_ms + this.duration_ms;
  }

  get has_error(): boolean {
    return this.error_span_ids.length > 0;
  }

  get error_anchor(): string {
    return this.error_span_ids[0] ?? this.primary_span_id;
  }
}
