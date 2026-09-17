import type { Component } from "./component.js";
import type { Environment } from "./environment.js";
import type { Workload } from "./workload.js";

/**
 * One Component's logical runtime presence in an Environment.
 * @spec Identity is scoped by name, Component and Environment, not platform resource names.
 * @rule Workload mappings may change without changing Service identity; declarations prove no live instances.
 * Consumers extend this model with their own capabilities and execution policy.
 */
export interface Service<E extends Environment = Environment> {
  readonly name: string;
  readonly component: Component;
  readonly environment: E;
  /** Zero or more runtime carriers; an empty array declares no workloads. */
  readonly workloads: readonly Workload[];
}
