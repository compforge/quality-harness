import type { Service } from "./service.js";

/** A category of resources persistently managed or used by a running Service.
 * @spec A declaration describes what can be inventoried, not the current count or existence.
 * @rule id identifies the same resource collection across Services; names are presentation only.
 */
export interface Resource {
  readonly id: string;
  readonly kind: string;
  readonly name: string;
  readonly description?: string;
}

/** Association does not transfer exclusive ownership or duplicate the underlying resource. */
export interface ServiceResource<S extends Service = Service> {
  readonly service: S;
  readonly resource: Resource;
  readonly relationship: "manages" | "uses";
}

/** Caller-owned environment and collection IDs disambiguate identical local instance IDs. */
export interface ResourceInstanceRef {
  readonly environment: string;
  readonly resource: string;
  readonly id: string;
}

export interface ResourceInstance<Details = Readonly<Record<string, unknown>>> {
  readonly ref: ResourceInstanceRef;
  readonly name?: string;
  /** Domain state, never an implicit health verdict. */
  readonly state?: string;
  readonly details?: Details;
}

/** Quantities retain their units and aggregation scope; different measures are not additive. */
export interface ResourceMeasurement {
  readonly name: string;
  readonly value: number;
  readonly unit: string;
  readonly scope: "observed" | "total";
}

export type ResourceCoverage =
  | { readonly status: "complete" }
  | { readonly status: "partial" | "unavailable"; readonly reason: string };

/**
 * Read-only inventory evidence. Collection, access, pagination policy and rendering belong to consumers.
 * @spec totalCount is an independently known count for scope, never inferred from a page of instances.
 * @spec Unknown values are omitted; partial or unavailable acquisition is not a resource health verdict.
 * @rule source, scope and details contain only safe serializable evidence, never access credentials.
 */
export interface ResourceSnapshot<Details = Readonly<Record<string, unknown>>> {
  readonly resource: Resource;
  readonly environment: string;
  readonly observedAt: string;
  readonly source: string;
  readonly scope: Readonly<Record<string, string>>;
  readonly coverage: ResourceCoverage;
  readonly totalCount?: number;
  readonly measurements?: readonly ResourceMeasurement[];
  readonly instances?: readonly ResourceInstance<Details>[];
  readonly nextCursor?: string;
}

export function sameResourceInstance(left: ResourceInstanceRef, right: ResourceInstanceRef): boolean {
  return left.environment === right.environment && left.resource === right.resource && left.id === right.id;
}
