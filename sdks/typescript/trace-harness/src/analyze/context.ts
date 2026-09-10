import type { TraceAnalysis } from "../loading/analysis";
import type { Node } from "../model/node";
import type { TraceContext } from "../model/context";
import { Measurements } from "../model/measurement";
import type { Finding } from "../model/node";

export class AnalysisContext {
  constructor(
    readonly trace: TraceContext,
    readonly measurements: Measurements = new Measurements(),
    readonly findings: Readonly<Record<string, readonly Finding[]>> = {},
    readonly runtime?: TraceAnalysis,
  ) {}
  async fact(node: Node, name: string): Promise<unknown> {
    return this.runtime ? this.runtime.fact(node, name) : node.facts[name];
  }
  async measure(node: Node, name: string) {
    return this.runtime ? this.runtime.metric(node, name) : this.measurements.get(node.node_id, name);
  }
}
