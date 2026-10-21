import { KubernetesError } from "../errors";

/** API resource names are plural REST names, not Kind names or kubectl aliases. */
export interface ResourceQuery {
  apiVersion: string;
  resource: string;
  namespace?: string;
}

export interface ResourceRef extends ResourceQuery { name: string }
export interface ResourceListQuery extends ResourceQuery {
  labelSelector?: string;
  fieldSelector?: string;
  limit?: number;
  continue?: string;
}

/** Preserve the server manifest; consumers own projections into domain models. */
export interface Resource {
  [field: string]: unknown;
  apiVersion?: string;
  kind?: string;
  metadata?: { name?: string; namespace?: string; uid?: string; [field: string]: unknown };
  spec?: { selector?: unknown; [field: string]: unknown };
}

export interface ResourceList {
  apiVersion?: string;
  kind?: string;
  metadata?: { continue?: string; resourceVersion?: string; [field: string]: unknown };
  items: Resource[];
  [field: string]: unknown;
}

export interface ResourceRequestOptions {
  signal?: AbortSignal;
  /** Total budget, including capacity wait, authentication and response consumption. */
  timeoutMs?: number;
}

export interface ResourceAccess {
  get(ref: ResourceRef, options?: ResourceRequestOptions): Promise<Resource>;
  list(query: ResourceListQuery, options?: ResourceRequestOptions): Promise<ResourceList>;
}

export interface ResourceLimits {
  timeoutMs: number;
  concurrency: number;
  maxBytes: number;
}

/** Namespace views borrow the root client and cannot close it or override their scope. */
export interface NamespaceResources {
  get(ref: Omit<ResourceRef, "namespace">, options?: ResourceRequestOptions): Promise<Resource>;
  list(query: Omit<ResourceListQuery, "namespace">, options?: ResourceRequestOptions): Promise<ResourceList>;
}

/** Explicit scope avoids interpreting an omitted namespace as a cluster-wide list. */
export function resourcePath(query: ResourceQuery, name?: string): string {
  const parts = query.apiVersion.split("/");
  if (parts.length > 2 || parts.some(part => !/^[a-z0-9][a-z0-9.-]*$/.test(part))
    || !/^[a-z][a-z0-9-]*$/.test(query.resource)
    || (query.namespace !== undefined && !/^[a-z0-9][a-z0-9-]*$/.test(query.namespace))
    || (name !== undefined && !/^[a-z0-9][a-z0-9.-]*$/.test(name))) {
    throw new KubernetesError("Invalid Kubernetes resource identity", { kind: "invalid_argument" });
  }
  const prefix = parts.length === 1 ? "/api/" : "/apis/";
  return prefix + parts.join("/") + (query.namespace === undefined ? "" : "/namespaces/" + query.namespace)
    + "/" + query.resource + (name === undefined ? "" : "/" + name);
}
