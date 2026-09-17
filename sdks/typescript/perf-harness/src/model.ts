import type { LoadPlan } from "./load";
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

import type {
  Service as BaseService,
  Execution,
  ExperimentRun,
} from "@compforge/harness-common";
export type {
  Operation,
  HttpOperation,
  OperationRun,
  Environment,
  Component,
  Repository,
  Forge,
} from "@compforge/harness-common";
export interface Service extends BaseService {
  base_url?: string;
  headers?: Record<string, string>;
}

export interface Arm {
  id: string;
  resources: ResourceProfile;
  load: LoadPlan;
}

export interface Outcome {
  status: number | null;
  duration_ms: number;
  events?: number;
  nbytes?: number;
  metrics?: Record<string, number>;
  meta?: Record<string, unknown>;
  facets?: Record<string, string>;
  case_id?: string;
}

export interface RequestEvaluation {
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
  arrived: number;
  dispatched: number;
  completed: number;
  succeeded: number;
  n_interrupted: number;
  arrival_rps: number;
  dispatch_rps: number;
  success_rps: number;
  inflight_peak: number;
  inflight_end: number;
  caveats: string[];
  metrics: Record<string, DistributionSummary>;
}

export type WindowKind = "measurement" | "ramp" | "hold" | "drain" | "cooldown";

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
  completed: number;
  errors: number;
  error_rate: number;
  threshold: number;
}

export interface ArmStop {
  reason: "deadline" | "error_rate" | "request_limit" | "aborted";
  snapshot?: StopSnapshot;
  inflight_at_stop: number;
  interrupted: number;
  force_cancelled: boolean;
}

export type Phase =
  "setup" | "measurement" | "deactivate" | "cooldown" | "cleanup";

export interface PhaseError {
  phase: Phase;
  error_type: string;
  message: string;
}

export interface RequestRecord {
  id: string;
  case_id: string;
  scheduled_at: number;
  arrived_at: number;
  dispatched_at?: number;
  finished_at?: number;
  state: "arrived" | "dispatched" | "finished" | "dropped" | "interrupted";
  reason?: string;
  operation_run_id?: string;
  facets: Record<string, string>;
}

export interface ArmRun extends Execution<Outcome> {
  id: string;
  service: string;
  arm: Arm;
  started_at: string;
  finished_at: string;
  windows: Window[];
  stop: ArmStop;
  slo: unknown[];
  registry: Record<string, unknown>;
  probe_errors: Record<string, unknown>;
  phase_errors: PhaseError[];
  requests: RequestRecord[];
  evaluations: Record<string, RequestEvaluation>;
}

export interface Run extends ExperimentRun<ArmRun> {
  schema: 5;
  service: string;
  passed: boolean;
}
