import type { Identity } from "./identity.js";
import type { Service } from "./system/service.js";

/** Capability identity is service-qualified and independent of access configuration. */
export class Operation implements Identity {
  readonly name: string;
  readonly serviceName: string;

  constructor(value: Omit<Operation, "id">) {
    this.name = value.name;
    this.serviceName = value.serviceName;
  }

  id(): string {
    if (!this.serviceName || !this.name) throw new Error("service and operation names must not be empty");
    const encode = (value: string) => encodeURIComponent(value).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
    return `operation:${encode(this.serviceName)}/${encode(this.name)}`;
  }
}

export class HttpOperation extends Operation {
  readonly method: string;
  readonly path: string;

  constructor(value: Omit<HttpOperation, "id">) {
    super(value);
    this.method = value.method;
    this.path = value.path;
  }
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
