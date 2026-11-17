import type { MetricDBBackend } from "../model";

export const prometheus: MetricDBBackend = {
  kind: "prometheus",
  queryParameters: () => ({}),
};
