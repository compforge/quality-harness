import type { Service } from "./service.js";

/** Stable capability identity, independent of access configuration. */
export interface Operation {
  readonly name: string;
}
export interface HttpOperation extends Operation {
  readonly method: string;
  readonly path: string;
}
/** A real call owns its raw domain evidence. Case identity and scheduling belong to the domain. */
export interface OperationRun<O> {
  readonly id: string;
  readonly service: Service;
  readonly operation: Operation;
  readonly outcome: O;
}
/** Domain-owned grouping and lifecycle; common does not prescribe a scheduler. */
export interface Execution<O> {
  readonly id: string;
  operation_runs: OperationRun<O>[];
}
export interface Artifact {
  readonly name: string;
  readonly path: string;
}
export interface Experiment {
  readonly name: string;
}
export interface ExperimentRun<E extends Execution<unknown>> {
  readonly run_id: string;
  readonly experiment: string;
  readonly created_at: string;
  executions: E[];
  artifacts: Artifact[];
}
