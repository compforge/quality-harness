import type { TraceContext } from "../model/context";
import { Measurements } from "../model/measurement";
import type { Finding } from "../model/node";

export class AnalysisContext {
  constructor(
    readonly trace: TraceContext,
    readonly measurements: Measurements = new Measurements(),
    readonly findings: Readonly<Record<string, readonly Finding[]>> = {},
  ) {}
}
