import { selectProbes, type Operation } from "@compforge/harness-common";
import { bindPrometheusProbe } from "@compforge/harness-toolbox/metricdb";
import { RED_UNITS, type PrometheusProbe, type WindowSelector } from "./model";

/** Adapt reusable Operation observations to the windows selected by this experiment. No I/O. */
export function operationMetricProbes(operations: readonly Operation[], backend: string,
  parameters: (operation: Operation) => Readonly<Record<string, string>> = () => ({}),
  windows: readonly WindowSelector[] = [{kind: "full"}, {kind: "phase", phase: "hold"}],
): PrometheusProbe[] {
  return operations.flatMap(operation => selectProbes(operation.probes ?? [], backend).map(declaration => {
    const probe = bindPrometheusProbe(declaration, parameters(operation));
    for (const [role, metric] of Object.entries(probe.metrics)) {
      if (Object.hasOwn(RED_UNITS, role) && metric.unit !== RED_UNITS[role as keyof typeof RED_UNITS]) throw new Error(`Invalid unit for ${role}`);
      if (metric.histogram && (!(metric.histogram.quantile > 0 && metric.histogram.quantile < 1)
        || !Number.isFinite(metric.histogram.scale) || metric.histogram.scale <= 0)) throw new Error("Invalid histogram projection");
    }
    const http = operation as Operation & {method?: string; path?: string; response_mode?: string};
    const metrics = Object.fromEntries(Object.keys(probe.metrics).map(role => [role, `${operation.id()}.${role}`]));
    return {service: operation.serviceName, target: operation.id(), method: http.method, path: http.path,
      response_mode: probe.responseMode === undefined ? http.response_mode : probe.responseMode, metrics,
      qps_semantics: probe.metrics.qps?.semantics === "entered" ? "entered" : "completed",
      summaries: Object.entries(probe.metrics).flatMap(([role, metric]) => windows.map(window => ({
        name: metrics[role]!, promql: metric.expression, unit: metric.unit,
        description: [metric.description, metric.semantics].filter(Boolean).join("; "), histogram: metric.histogram, window,
      }))),
    };
  }));
}
