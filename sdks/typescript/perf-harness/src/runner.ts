import type { Case } from "@compforge/spec-case/model";
import type { Arm, Outcome, Service, Operation } from "./model";

export interface ArmContext {
  service: Service;
  arm: Arm;
  run_id: string;
  signal: AbortSignal;
}

export interface FireContext extends ArmContext {
  case: Case;
}

export interface Runner {
  name: string;
  operation?(context: FireContext): Operation;
  setup?(context: ArmContext): Promise<void>;
  fire(context: FireContext): Promise<Outcome>;
  deactivate?(context: ArmContext): Promise<void>;
  cleanup?(context: ArmContext): Promise<void>;
}
