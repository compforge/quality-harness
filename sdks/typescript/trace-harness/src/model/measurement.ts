export interface MeasurementSpec {
  kind: string;
  scope: "node" | "trace";
  units: Record<string, string>;
  description: string;
  dimensions: string[];
}
export interface CallSource {
  id: string;
  kind: string;
  start_ms: number;
  end_ms: number;
  node_id: string;
  span_ids: string[];
}
export interface Measurement {
  kind: string;
  node_id: string | null;
  status: "measured" | "not_applicable" | "error";
  values: Record<string, unknown>;
  evidence: Record<string, unknown>;
  error: string | null;
}
export class Measurements {
  constructor(
    readonly specs: MeasurementSpec[] = [],
    readonly sources: CallSource[] = [],
    readonly nodes: Record<string, Measurement[]> = {},
    readonly trace: Measurement[] = [],
  ) {}
  get(nodeId: string | null, kind: string): Measurement | undefined {
    return (nodeId === null ? this.trace : this.nodes[nodeId])?.find((result) => result.kind === kind);
  }
}
