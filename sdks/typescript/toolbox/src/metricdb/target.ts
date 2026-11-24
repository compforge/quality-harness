import type { ScrapeTarget, Service } from "@compforge/harness-common";
import { parseServices, type KubernetesService } from "../kubernetes/service";

import type { Resource } from "../kubernetes/resources";

type ServiceSelector = Pick<KubernetesService, "name" | "namespace" | "selector">;

function podMatches(selector: Readonly<Record<string, string>>, labels: Readonly<Record<string, string>>): boolean {
  const entries = Object.entries(selector);
  return entries.length > 0 && entries.every(([key, value]) =>
    labels["__meta_kubernetes_pod_label_" + key.replace(/[^a-zA-Z0-9_]/g, "_")] === value);
}

/** Match discovery labels against the declared Kubernetes workload in its namespace. */
export function matchesScrapeTarget(service: Pick<Service, "name" | "workloads">, namespace: string,
  target: ScrapeTarget, services: readonly ServiceSelector[] = []): boolean {
  const meta = target.discoveredLabels ?? {};
  const labels = target.labels ?? {};
  const ns = meta.__meta_kubernetes_namespace ?? labels.namespace;
  const serviceName = meta.__meta_kubernetes_service_name ?? labels.service;
  const matchesService = (name: string, workloadNamespace: string): boolean => {
    if (ns !== workloadNamespace) return false;
    if (serviceName) return serviceName === name;
    // A Pod scrape can omit Service metadata. Use the observed Service's full selector.
    return !!meta.__meta_kubernetes_pod_name && services.some(candidate =>
      candidate.namespace === workloadNamespace && candidate.name === name && podMatches(candidate.selector, meta));
  };
  if (!service.workloads.length) return matchesService(service.name, namespace);
  return service.workloads.some(workload => {
    if (ns !== (workload.namespace ?? namespace)) return false;
    const location = workload.location;
    if (location.kind === "service") return matchesService(location.name, workload.namespace ?? namespace);
    if (location.kind === "labels") return podMatches(location.labels, meta);
    if (location.resource_kind === "Pod") return (meta.__meta_kubernetes_pod_name ?? labels.pod) === location.name;
    return meta.__meta_kubernetes_pod_controller_kind === location.resource_kind
      && meta.__meta_kubernetes_pod_controller_name === location.name;
  });
}

/** Resolve only Service selectors needed for Pod targets. Preserve direct matches if an optional read fails. */
export async function matchServiceScrapeTargets(service: Pick<Service, "name" | "workloads">, namespace: string,
  targets: readonly ScrapeTarget[], readService: (ref: {name:string; namespace:string}) => Promise<Resource>) {
  const refs = service.workloads.length ? service.workloads.flatMap(workload => workload.location.kind === "service"
    ? [{name:workload.location.name, namespace:workload.namespace ?? namespace}] : []) : [{name:service.name, namespace}];
  const podNamespaces = new Set(targets.filter(target => target.discoveredLabels?.__meta_kubernetes_pod_name
    && !target.discoveredLabels.__meta_kubernetes_service_name && !target.labels?.service)
    .map(target => target.discoveredLabels?.__meta_kubernetes_namespace ?? target.labels?.namespace));
  const needed = new Map(refs.filter(ref => podNamespaces.has(ref.namespace)).map(ref => [ref.namespace + "/" + ref.name, ref]));
  const reads = await Promise.all([...needed.values()].map(async ref => {
    try {
      const item = await readService(ref);
      return {selectors:parseServices(JSON.stringify({items:[item]}), ref.namespace)};
    } catch (error) { return {selectors:[], failure:{...ref, error}}; }
  }));
  const selectors = reads.flatMap(read => read.selectors);
  return {targets:targets.filter(target => matchesScrapeTarget(service, namespace, target, selectors)),
    failures:reads.flatMap(read => read.failure ? [read.failure] : [])};
}
