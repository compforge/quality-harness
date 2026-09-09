export interface MeasurementSpec {
  id: string;
  scope: "node" | "trace_prefix";
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
  spec_id: string;
  anchor_node_id: string;
  status: "measured" | "not_applicable" | "error";
  values: Record<string, unknown>;
  evidence: Record<string, unknown>;
  error: string | null;
}
export class Measurements {
  constructor(
    readonly specs: MeasurementSpec[] = [],
    readonly sources: CallSource[] = [],
    readonly results: Record<string, Measurement[]> = {},
  ) {}
  get(nodeId: string, specId: string): Measurement | undefined {
    return this.results[nodeId]?.find((result) => result.spec_id === specId);
  }
}
