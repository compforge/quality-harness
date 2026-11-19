import { serviceId, type ObservationDefinition, type Operation } from "@compforge/harness-common";
import { bindMetricDBObservations } from "@compforge/harness-toolbox/metricdb";
import { RED_UNITS, type PrometheusProbe, type WindowQuery, type WindowSelector } from "./model";

/** Attach a resolved MetricDB observation to a target and the experiment's selected windows. */
export function metricObservationQueries(target: string, observation: ObservationDefinition,
  windows: readonly WindowSelector[] = [{kind: "full"}],
): WindowQuery[] {
  if (observation.type !== "metricdb") throw new Error(`Expected a MetricDB observation for ${target}`);
  return Object.entries(observation.metrics).flatMap(([name, metric]) => windows.map(window => ({
    name: `${target}.${name}`, promql: metric.expression, category: metric.category, unit: metric.unit,
    trendWindowSeconds: metric.trendWindowSeconds, histogram: metric.histogram, window,
    description: [metric.description, metric.semantics].filter(Boolean).join("; "),
  })));
}

/** Adapt reusable Operation observations to the windows selected by this experiment. No I/O. */
export function operationMetricProbes(operations: readonly Operation[], backend: string,
  parameters: (operation: Operation) => Readonly<Record<string, string>> = () => ({}),
  windows: readonly WindowSelector[] = [{kind: "full"}, {kind: "phase", phase: "hold"}],
): PrometheusProbe[] {
  return operations.flatMap(operation => bindMetricDBObservations(operation.observes ?? [], backend, parameters(operation)).map(observation => {
    const metrics: Record<string, string> = Object.create(null);
    let qpsSemantics: "entered" | "completed" = "completed";
    for (const [name, metric] of Object.entries(observation.metrics)) {
      const category = metric.category;
      const red = Object.keys(RED_UNITS).find(role => category === `red:${role}`);
      // The binding key drives Callgraph RED slots. Preserve the declared series name independently.
      const role = red ?? (Object.hasOwn(RED_UNITS, name) || name.startsWith("metric:") ? `metric:${name}` : name);
      if (Object.hasOwn(metrics, role)) throw new Error(`Duplicate metric role ${role} for ${operation.id()}`);
      metrics[role] = `${operation.id()}.${name}`;
      if (red === "qps" && metric.semantics === "entered") qpsSemantics = "entered";
      if (Object.hasOwn(RED_UNITS, role) && metric.unit !== RED_UNITS[role as keyof typeof RED_UNITS]) throw new Error(`Invalid unit for ${role}`);
      if (metric.histogram && (!(metric.histogram.quantile > 0 && metric.histogram.quantile < 1)
        || !Number.isFinite(metric.histogram.scale) || metric.histogram.scale <= 0)) throw new Error("Invalid histogram projection");
    }
    const http = operation as Operation & {method?: string; path?: string; response_mode?: string};
    return {service: operation.serviceName, target: operation.id(), method: http.method, path: http.path,
      response_mode: observation.responseMode === undefined ? http.response_mode : observation.responseMode, metrics,
      qps_semantics: qpsSemantics,
      summaries: metricObservationQueries(operation.id(), observation, windows),
    };
  }));
}

/** A workload's name is local to its Service. Both labels travel with every saved sample. */
export function workloadObservationTarget(service: string, workload: string): string {
  if (!workload) throw new Error("Workload observation requires a name");
  return `workload:${encodeURIComponent(service)}/${encodeURIComponent(workload)}`;
}

export function serviceMetricProbes(service: string, declarations: readonly ObservationDefinition[], backend: string,
  workload?: string, parameters: Readonly<Record<string, string>> = {},
): PrometheusProbe[] {
  const target = workload === undefined ? serviceId(service) : workloadObservationTarget(service, workload);
  return bindMetricDBObservations(declarations, backend, parameters).map(observation => ({
    service, target, scope: workload === undefined ? "service" : "workload", workload,
    metrics: {}, summaries: metricObservationQueries(target, observation),
  }));
}
