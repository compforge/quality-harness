import type { ProbeDefinition } from "@compforge/harness-common";

/** @spec Parameters are label string values, never executable PromQL fragments or credentials. */
export function bindMetricDBProbe(probe: ProbeDefinition, parameters: Readonly<Record<string, string>>): ProbeDefinition {
  if (probe.type !== "metricdb") throw new Error(`Unsupported query probe type: ${probe.type}`);
  return {...probe, metrics: Object.fromEntries(Object.entries(probe.metrics).map(([role, metric]) => {
    if (!role || !metric.unit || !metric.expression?.trim()) throw new Error("Metric requires role, unit and expression");
    const expression = metric.expression.replace(/\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g, (_match, name: string) => {
      if (!Object.hasOwn(parameters, name) || typeof parameters[name] !== "string" || !parameters[name])
        throw new Error(`Missing metric scope parameter: ${name}`);
      return JSON.stringify(parameters[name]);
    });
    if (expression.includes("{{") || expression.includes("}}")) throw new Error("Invalid metric scope placeholder");
    return [role, {...metric, expression}];
  }))};
}
