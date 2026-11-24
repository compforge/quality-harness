export { MetricDBError } from "../errors";
export { MetricDBClient, type MetricEndpoint, type MetricDBOptions } from "./client";
export type { MetricDBKind, MetricDBReader, ScrapeReader, MetricDBResult } from "./model";
export { bindMetricDBObservations, bindMetricDBObservation, bindMetricDBObservation as bindMetricDBProbe, counterIncreaseFunction } from "./observation";
export { workloadResourceObservation } from "./workload";
export { matchesScrapeTarget } from "./target";
export { vmSelectTenant, listVMAgentInstances, findVMAgentCollectors, findPrometheusCollectors,
  type MetricCollector, type VMAgentCollector, type PrometheusCollector } from "./victoriametrics/collector";
