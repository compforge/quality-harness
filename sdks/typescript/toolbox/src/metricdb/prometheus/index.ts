import type { MetricCollector, MetricDBBackend } from "../model";

/** A Prometheus Server that writes to the matched VictoriaMetrics cluster and tenant. */
export interface PrometheusCollector extends MetricCollector {
  kind: "prometheus";
  vmselect: string;
  vminsert: string;
  tenant: string;
}

export const prometheus: MetricDBBackend = {
  kind: "prometheus",
  queryParameters: () => ({}),
};
