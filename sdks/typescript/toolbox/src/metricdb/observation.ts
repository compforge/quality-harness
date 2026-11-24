import { MetricDBError } from "../errors";
import { selectObservations, type ObservationDefinition } from "@compforge/harness-common";

import type { MetricDBKind } from "./model";

/** Both variants use Prometheus counter-increase semantics for the same declared measurement. */
export const counterIncreaseFunction = {
  prometheus: "increase",
  victoriametrics: "increase_prometheus",
} as const satisfies Record<MetricDBKind, string>;

/** Select and bind declarations for this reader, leaving the execution window to its consumer. */
export function bindMetricDBObservations(declarations: readonly ObservationDefinition[], backend: string,
  parameters: Readonly<Record<string, string>> = {},
): readonly ObservationDefinition[] {
  const selected = selectObservations(declarations.filter(d => d.type === "metricdb"), backend);
  return selected.map(observation => bindMetricDBObservation(observation, parameters));
}

/** @spec Parameters are label string values, never executable PromQL fragments or credentials. */
export function bindMetricDBObservation(observation: ObservationDefinition, parameters: Readonly<Record<string, string>>): ObservationDefinition {
  if (observation.type !== "metricdb") throw new MetricDBError(`Unsupported observation type: ${observation.type}`, {kind: "invalid_argument"});
  return {...observation, metrics: Object.fromEntries(Object.entries(observation.metrics).map(([role, metric]) => {
    if (!role || !metric.unit || !metric.expression?.trim()) throw new MetricDBError("Metric requires role, unit and expression", {kind: "invalid_argument"});
    const expression = metric.expression.replace(/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g, (_match, name: string) => {
      if (!Object.hasOwn(parameters, name) || typeof parameters[name] !== "string" || !parameters[name])
        throw new MetricDBError(`Missing metric scope parameter: ${name}`, {kind: "invalid_argument"});
      return JSON.stringify(parameters[name]);
    });
    if (expression.includes("{{") || expression.includes("}}")) throw new MetricDBError("Invalid metric scope placeholder", {kind: "invalid_argument"});
    return [role, {...metric, expression}];
  }))};
}
