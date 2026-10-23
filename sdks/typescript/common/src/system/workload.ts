/** Logical carrier, not proof of existence. Location is data, not a discovery executor. */
export interface Workload {
  readonly name: string;
  /** Safe role/context for discovery; not identity, location or proof of availability. */
  readonly description?: string;
  readonly platform: "kubernetes";
  /** Omission requires a resolver default; never means all namespaces. */
  readonly namespace?: string;
  readonly location:
    | { readonly kind: "resource"; readonly resource_kind: "Deployment" | "StatefulSet" | "DaemonSet" | "Pod"; readonly name: string }
    | { readonly kind: "service"; readonly name: string }
    | { readonly kind: "labels"; readonly labels: Readonly<Record<string, string>> };
  /** Operation default, not workload identity. */
  readonly container?: string;
}

/**
 * Observed Pod incarnation. No access credentials belong in instance evidence.
 * @spec Environment and UID separate clusters and same-name Pod replacements.
 * @rule Container participates in access/cache keys, not Pod identity.
 */
export interface WorkloadInstance {
  readonly platform: "kubernetes";
  /** Stable caller-owned key distinguishing clusters/contexts, not a display label. */
  readonly environment: string;
  /** Declaration name scoped by owning Service; provenance, not Pod identity. */
  readonly workload: string;
  readonly namespace: string;
  readonly pod: string;
  readonly uid: string;
  readonly container?: string;
}

/** Physical identity only; callers retain workload provenance separately. */
export function sameWorkloadInstance(left: WorkloadInstance, right: WorkloadInstance): boolean {
  return left.platform === right.platform
    && left.environment === right.environment
    && left.namespace === right.namespace
    && left.pod === right.pod
    && left.uid === right.uid;
}
