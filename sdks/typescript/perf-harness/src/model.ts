import type { LoadProfile } from "./load";
export type { Case, CaseSet } from "@compforge/spec-case/model";

export interface CaseMixEntry {
  id: string;
  weight?: number;
}

export interface ResourceProfile {
  cpu?: string;
  memory?: string;
  workers?: number;
  replicas?: number;
  extra?: Record<string, string>;
}

export interface Forge {
  name: string;
}

export interface Repository {
  forge: Forge;
  path: string;
}

export interface Product {
  name: string;
}

export interface Component {
  repository: Repository;
  name: string;
}

export interface Environment {
  name: string;
}

export interface KubernetesEnvironment extends Environment {
  kubeconfig: string;
  context?: string;
}

export interface Service {
  name: string;
  component?: Component;
  environment?: KubernetesEnvironment;
  base_url?: string;
  headers?: Record<string, string>;
  namespace?: string;
  k8s_selector?: string;
  container?: string;
}

export interface Operation {
  name: string;
}

export interface HttpOperation extends Operation {
  method: string;
  path: string;
}

export interface Arm {
  id: string;
  resources: ResourceProfile;
  load: LoadProfile;
}

export interface Outcome {
  status: number | null;
  duration_ms: number;
  ok?: boolean;
  error_kind?: string;
  events?: number;
  nbytes?: number;
  metrics?: Record<string, number>;
  dropped?: boolean;
  meta?: Record<string, unknown>;
  facets?: Record<string, string>;
  case_id?: string;
}

export interface Verdict {
  ok: boolean;
  error_kind?: string;
}

export interface DistributionSummary {
  kind: "distribution";
  n: number;
  mean: number;
  p50: number;
  p95: number;
  p99: number;
  caveats: string[];
}

export interface RequestStats {
  n: number;
  n_ok: number;
  throughput_rps: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  mean_ms: number;
  error_rate: number;
  error_breakdown: Record<string, number>;
  n_dropped: number;
  caveats: string[];
  metrics: Record<string, DistributionSummary>;
}

export type WindowKind = "measurement" | "ramp" | "hold" | "cooldown";

export interface Window {
  id: string;
  name: string;
  kind: WindowKind;
  start_s: number;
  end_s: number;
  complete: boolean;
  target_level?: number;
  request?: RequestStats;
  by_case: Record<string, RequestStats>;
  by_facet: Record<string, Record<string, RequestStats>>;
  probe_metrics: Record<string, never>;
}

export interface StopSnapshot {
  at_s: number;
  sent: number;
  errors: number;
  error_rate: number;
  threshold: number;
}

export interface TrialStop {
  reason: "deadline" | "error_rate" | "request_limit" | "aborted";
  snapshot?: StopSnapshot;
  inflight_at_stop: number;
  interrupted: number;
  force_cancelled: boolean;
}

export type Phase = "setup" | "measurement" | "deactivate" | "cooldown" | "cleanup";

export interface PhaseError {
  phase: Phase;
  error_type: string;
  message: string;
}

export interface TimedOutcome {
  t: number;
  outcome: Outcome;
}

export interface TrialRecord {
  id: string;
  service: string;
  arm: Arm;
  started_at: string;
  finished_at: string;
  windows: Window[];
  stop: TrialStop;
  slo: unknown[];
  registry: Record<string, unknown>;
  probe_errors: Record<string, unknown>;
  phase_errors: PhaseError[];
  outcomes: TimedOutcome[];
}

export interface Run {
  schema: 4;
  run_id: string;
  experiment: string;
  created_at: string;
  service: string;
  passed: boolean;
  n_trials: number;
  trials: TrialRecord[];
}
