import type { MetricCollector, MetricDBBackend } from "../model";

export interface VMAgentCollector extends MetricCollector {
  kind: "vmagent";
  agent: string;
}

/**
 * @spec Keep frozen evaluation times and range grids; sample arrival is a separate concern.
 * @why nocache prevents instant shifts and grid alignment. Range tail adjustment separately
 * uses latency_offset; 1ms is the minimum accepted by older VM versions.
 * @see https://github.com/VictoriaMetrics/VictoriaMetrics/issues/241
 */
export const victoriaMetrics: MetricDBBackend = {
  kind: "victoriametrics",
  queryParameters: () => ({nocache: "1", latency_offset: "1ms"}),
};
