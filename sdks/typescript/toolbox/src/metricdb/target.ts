import type { ScrapeTarget, Service } from "@compforge/harness-common";

/** Match discovery labels against the declared Kubernetes workload in its namespace. */
export function matchesScrapeTarget(service: Pick<Service, "name" | "workloads">, namespace: string, target: ScrapeTarget): boolean {
  const meta = target.discoveredLabels ?? {};
  const labels = target.labels ?? {};
  const ns = meta.__meta_kubernetes_namespace ?? labels.namespace;
  const serviceName = meta.__meta_kubernetes_service_name ?? labels.service;
  if (!service.workloads.length) return ns === namespace && serviceName === service.name;
  return service.workloads.some(workload => {
    if (ns !== (workload.namespace ?? namespace)) return false;
    const location = workload.location;
    if (location.kind === "service") return serviceName === location.name;
    if (location.kind === "labels") {
      const entries = Object.entries(location.labels);
      return entries.length > 0 && entries.every(([key, value]) =>
        meta["__meta_kubernetes_pod_label_" + key.replace(/[^a-zA-Z0-9_]/g, "_")] === value);
    }
    if (location.resource_kind === "Pod") return (meta.__meta_kubernetes_pod_name ?? labels.pod) === location.name;
    return meta.__meta_kubernetes_pod_controller_kind === location.resource_kind
      && meta.__meta_kubernetes_pod_controller_name === location.name;
  });
}
