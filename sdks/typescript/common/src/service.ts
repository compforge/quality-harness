import type { Knowledge } from "./knowledge.js";
import type { Component } from "./component.js";
import type { Environment } from "./environment.js";
import type { Workload } from "./workload.js";

/**
 * One Component's logical runtime presence in an Environment.
 * @spec Identity is scoped by name, Component and Environment, not platform resource names.
 * @rule Workload mappings may change without changing Service identity; declarations prove no live instances.
 * @spec Description and Knowledge are discovery metadata, independent of identity and runtime evidence.
 * @see {@link component://docs/kernel.md}
 * Consumers extend this model with their own capabilities and execution policy.
 */
export interface Service<E extends Environment = Environment> {
  readonly name: string;
  /** Structured offline knowledge; not part of identity or runtime evidence. */
  readonly knowledge?: Knowledge;
  /** @deprecated Declare domain objects, relationships and constraints in knowledge.concepts. */
  readonly concepts?: string;
  /** @deprecated Declare business processes in knowledge.workflows. */
  readonly workflows?: string;
  /** Human-readable discovery metadata; not part of Service identity. */
  readonly description?: string;
  readonly component: Component;
  readonly environment: E;
  /** Zero or more runtime carriers; an empty array declares no workloads. */
  readonly workloads: readonly Workload[];
}
