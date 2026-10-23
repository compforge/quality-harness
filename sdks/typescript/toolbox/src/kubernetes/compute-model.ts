import type { ErrorKind } from "../errors";

/** CPU cores and bytes; never percentages or unitless Kubernetes quantity strings. */
export interface ComputeQuantity { cpu: number; memory: number }
export interface ComputeIssue {
  source: string;
  kind: ErrorKind;
  message: string;
  code?: string | number;
}
export interface ComputeRead<T> {
  status: "complete" | "partial" | "unavailable";
  items: T[];
  issues: ComputeIssue[];
}
export interface ComputeUsage extends ComputeQuantity {
  timestamp: string;
  window: string;
}
export interface NodeCompute {
  name: string;
  uid: string;
  ready: boolean;
  unschedulable: boolean;
  capacity: ComputeQuantity;
  allocatable: ComputeQuantity;
}
export interface PodCompute {
  namespace: string;
  name: string;
  uid: string;
  createdAt?: string;
  node?: string;
  phase: string;
  ready: boolean;
  restarts: number;
  /** Steady-state application and native sidecar container names, for metrics coverage. */
  containers: string[];
  /** Spec-based effective requests, including init/sidecar peak, Pod-level overrides and overhead. */
  requests: ComputeQuantity;
  /** Sum/peak of declared limits, NOT an upper bound when containers have no limit. */
  configuredLimits: ComputeQuantity;
  /** Container counts, not cores/bytes; a Pod-level budget can still apply. */
  containersWithoutLimits: { cpu: number; memory: number };
}
export interface NodeComputeUsage extends ComputeUsage { name: string }
export interface PodComputeUsage extends ComputeUsage { namespace: string; name: string; uid: string }
export interface PodComputeSummary {
  pods: number;
  activePods: number;
  scheduledPods: number;
  readyPods: number;
  /** Active Pods only; unscheduled requests are demand, not allocated node capacity. */
  requests: ComputeQuantity;
  scheduledRequests: ComputeQuantity;
  configuredLimits: ComputeQuantity;
  /** Container counts, not cores/bytes; a Pod-level budget can still apply. */
  containersWithoutLimits: { cpu: number; memory: number };
}
export interface ClusterCompute {
  environment: string;
  observedAt: string;
  scope: "cluster";
  nodes: ComputeRead<NodeCompute>;
  pods: ComputeRead<PodCompute>;
  usage: ComputeRead<NodeComputeUsage>;
  /** Totals describe acquired evidence only; consult each read's coverage before claiming cluster totals. */
  summary: {
    capacity?: ComputeQuantity;
    allocatable?: ComputeQuantity;
    pods?: PodComputeSummary;
    usage?: ComputeQuantity;
  };
}
export interface WorkloadCompute {
  environment: string;
  observedAt: string;
  /** Scope is exactly the supplied instance set, not a claim of complete Service discovery. */
  scope: "instances";
  workloads: readonly { namespace: string; workload: string; pod: string; uid: string }[];
  pods: ComputeRead<PodCompute>;
  usage: ComputeRead<PodComputeUsage>;
  summary: { pods?: PodComputeSummary; usage?: ComputeQuantity };
}
export interface ComputeOptions {
  environment: string;
  signal?: AbortSignal;
  /** Whole collection budget, including all pages and metrics reads. */
  timeoutMs: number;
  maxPods: number;
  maxNodes: number;
  pageSize: number;
}
