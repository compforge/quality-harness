import type { PrometheusCollector } from "./prometheus";
import type { VMAgentCollector } from "./victoriametrics";

export { MetricDBError } from "../errors";
export { MetricDBClient, type MetricEndpoint, type MetricDBOptions } from "./client";
export type { MetricDBKind, MetricDBReader, ScrapeReader, MetricDBResult } from "./model";
export type { PrometheusCollector, VMAgentCollector };
export type MetricCollector = PrometheusCollector | VMAgentCollector;
export { bindMetricDBObservations, bindMetricDBObservation, bindMetricDBObservation as bindMetricDBProbe, counterIncreaseFunction } from "./observation";
export { workloadResourceObservation } from "./workload";
export { matchesScrapeTarget } from "./target";
export { vmSelectTenant, listVMAgentInstances, findVMAgentCollectors, findPrometheusCollectors } from "./victoriametrics/collector";
