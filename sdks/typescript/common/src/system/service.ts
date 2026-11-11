import type { Identity } from "../identity.js";
import type { Knowledge } from "./knowledge.js";
import type { Component } from "./component.js";
import type { Environment } from "./environment.js";
import type { Workload } from "./workload.js";

/**
 * One Component's logical runtime presence in an Environment.
 * @spec Identity is scoped by name, Component and Environment, not platform resource names.
 * @rule Workload mappings may change without changing Service identity; declarations prove no live instances.
 * @spec Description and Knowledge are discovery metadata, independent of identity and runtime evidence.
 * @spec upstream names callers of this Service; omitted is undeclared and an empty list declares no callers.
 * @see {@link component://docs/system-model.md}
 * Consumers extend this model with their own capabilities and execution policy.
 */
export class Service<E extends Environment = Environment> implements Identity {
  readonly name: string;
  /** Structured offline knowledge; not part of identity or runtime evidence. */
  readonly knowledge?: Knowledge;
  /**
   * Canonical names of Services that call this Service, resolved in the consumer's catalog.
   * Omitted means undeclared; an empty list declares no upstream callers.
   * This is offline architecture metadata, independent of identity and observed traffic.
   */
  readonly upstream?: readonly string[];
  /** Human-readable discovery metadata; not part of Service identity. */
  readonly description?: string;
  readonly component: Component;
  readonly environment: E;
  /** Zero or more runtime carriers; an empty array declares no workloads. */
  readonly workloads: readonly Workload[];

  constructor(value: Omit<Service<E>, "id">) {
    this.name = value.name;
    this.component = value.component;
    this.environment = value.environment;
    this.workloads = value.workloads;
    this.description = value.description;
    this.knowledge = value.knowledge;
    this.upstream = value.upstream;
  }

  /** Name-based identity scoped to one environment's service catalog. */
  id(): string {
    if (!this.name) throw new Error("service name must not be empty");
    const name = encodeURIComponent(this.name).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
    return `service:${name}`;
  }
}
