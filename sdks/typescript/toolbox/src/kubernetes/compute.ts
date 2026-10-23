import type { WorkloadInstance } from "@compforge/harness-common";
import { KubernetesError, type ErrorKind } from "../errors";
import type { Resource, ResourceAccess, ResourceListQuery, ResourceRequestOptions } from "./resources";
import type { ClusterCompute, ComputeOptions, ComputeRead, ComputeIssue, ComputeUsage,
  NodeCompute, NodeComputeUsage, PodCompute, PodComputeUsage, WorkloadCompute } from "./compute-model";
import { object, requiredString, quantities, sum, podCompute, summarizePods } from "./compute-quantity";
export type * from "./compute-model";
export { parseComputeQuantity } from "./compute-quantity";

type Request = () => ResourceRequestOptions;
function requestBudget(options: ComputeOptions): Request {
  if (!options.environment.trim() || [options.timeoutMs, options.maxPods, options.maxNodes, options.pageSize]
    .some(value => !Number.isSafeInteger(value) || value <= 0)) {
    throw new KubernetesError("Compute requires environment and positive timeout/item/page budgets", { kind: "invalid_argument" });
  }
  const deadline = performance.now() + options.timeoutMs;
  return () => {
    options.signal?.throwIfAborted();
    const remaining = Math.ceil(deadline - performance.now());
    if (remaining <= 0) throw new KubernetesError("Compute collection budget exhausted", { kind: "timeout" });
    return { signal: options.signal, timeoutMs: remaining };
  };
}
function issue(source: string, error: unknown): ComputeIssue {
  // Raw transport errors can contain URLs/credentials. Only stable categories cross this evidence boundary.
  const kind: ErrorKind = error instanceof KubernetesError ? error.kind : "operation_failed";
  return { source, kind, code: error instanceof KubernetesError ? error.code : undefined,
    message: kind === "permission_denied" ? "Permission denied" : `Kubernetes compute read failed: ${kind}` };
}
function finish<T>(items: T[], issues: ComputeIssue[], observed: boolean): ComputeRead<T> {
  return { items, issues, status: !issues.length ? "complete" : observed ? "partial" : "unavailable" };
}
function gap(source: string, kind: ErrorKind, message: string): ComputeIssue { return { source, kind, message }; }

/** Every page uses the shared deadline. A late failure retains earlier evidence and never claims a full list. */
async function readPages<T>(access: ResourceAccess, query: ResourceListQuery, maxItems: number,
  options: ComputeOptions, request: Request, parse: (item: Resource) => T, identity: (item: T) => string): Promise<ComputeRead<T>> {
  const source = `${query.apiVersion}/${query.resource}`;
  const items: T[] = [], issues: ComputeIssue[] = [];
  const tokens = new Set<string>(), identities = new Set<string>();
  let continuation: string | undefined, observed = false, read = 0;
  try {
    do {
      const page = await access.list({ ...query, limit: Math.min(options.pageSize, maxItems - read), continue: continuation }, request());
      if (!Array.isArray(page.items)) throw new KubernetesError("List lacks items", { kind: "invalid_response" });
      observed = true;
      for (const item of page.items.slice(0, maxItems - read)) {
        try {
          const parsed = parse(item), key = identity(parsed);
          if (!identities.has(key)) { items.push(parsed); identities.add(key); }
        } catch (error) { issues.push(issue(source, error)); }
      }
      read += page.items.length;
      continuation = page.metadata?.continue || undefined;
      if (read > maxItems || (read >= maxItems && continuation)) {
        issues.push(gap(source, "limit_exceeded", `Collection limited to ${maxItems} items`)); break;
      }
      if (continuation && tokens.has(continuation)) {
        issues.push(gap(source, "invalid_response", "Pagination did not advance")); break;
      }
      if (continuation) tokens.add(continuation);
    } while (continuation);
  } catch (error) {
    options.signal?.throwIfAborted();
    issues.push(issue(source, error));
  }
  return finish(items, issues, observed);
}
function nodeCompute(value: Resource): NodeCompute {
  const meta = object(value.metadata), status = object(value.status), spec = object(value.spec);
  const conditions = Array.isArray(status.conditions) ? status.conditions.map(object) : [];
  return { name: requiredString(meta.name, "node name"), uid: requiredString(meta.uid, "node uid"),
    ready: conditions.some(c => c.type === "Ready" && c.status === "True"), unschedulable: spec.unschedulable === true,
    capacity: quantities(status.capacity, true), allocatable: quantities(status.allocatable, true) };
}
function usage(value: Resource): ComputeUsage {
  const timestamp = requiredString(value.timestamp, "metrics timestamp"), window = requiredString(value.window, "metrics window");
  if (!Number.isFinite(Date.parse(timestamp))) throw new KubernetesError("Invalid metrics timestamp", { kind: "invalid_response" });
  return { ...quantities(value.usage, true), timestamp, window };
}

/**
 * @spec Cluster reads are independent: forbidden Nodes/metrics are coverage gaps, not cluster-health failures.
 * @rule access must be an explicit cluster-scoped view, e.g. KubernetesClient.clusterResources().
 * No namespace fallback, client creation or cleanup occurs here.
 */
export async function collectClusterCompute(access: ResourceAccess, options: ComputeOptions): Promise<ClusterCompute> {
  const request = requestBudget(options);
  const nodes = await readPages(access, { apiVersion: "v1", resource: "nodes" }, options.maxNodes, options, request, nodeCompute, n => n.uid);
  const pods = await readPages(access, { apiVersion: "v1", resource: "pods" }, options.maxPods, options, request, podCompute, p => `${p.namespace}/${p.uid}`);
  const metrics = await readPages<NodeComputeUsage>(access, { apiVersion: "metrics.k8s.io/v1beta1", resource: "nodes" },
    options.maxNodes, options, request, item => ({ ...usage(item), name: requiredString(item.metadata?.name, "metrics node name") }), n => n.name);
  if (nodes.status !== "unavailable" && metrics.status !== "unavailable") {
    const measured = new Set(metrics.items.map(item => item.name));
    for (const node of nodes.items) if (!measured.has(node.name)) {
      metrics.issues.push(gap(`nodes/${node.name}`, "resource_not_found", "Node has no metrics sample"));
    }
    if (metrics.issues.length) metrics.status = "partial";
  }
  options.signal?.throwIfAborted();
  return { environment: options.environment, observedAt: new Date().toISOString(), scope: "cluster", nodes, pods, usage: metrics,
    summary: { capacity: nodes.status !== "complete" && !nodes.items.length ? undefined : sum(nodes.items.map(n => n.capacity)),
      allocatable: nodes.status !== "complete" && !nodes.items.length ? undefined : sum(nodes.items.map(n => n.allocatable)),
      pods: pods.status !== "complete" && !pods.items.length ? undefined : summarizePods(pods.items),
      usage: metrics.status === "unavailable" || (!metrics.items.length && (metrics.status !== "complete" || nodes.status !== "complete" || nodes.items.length > 0)) ? undefined : sum(metrics.items) } };
}

/**
 * @spec Whole-Pod accounting deduplicates physical UID across Workload aliases and container defaults.
 * @spec Only caller-supplied instances are read; cluster permissions are not needed.
 * @rule The caller retains discovery coverage. This result never asserts that input covers all Service instances.
 */
export async function collectWorkloadCompute(access: ResourceAccess, instances: readonly WorkloadInstance[],
  options: ComputeOptions): Promise<WorkloadCompute> {
  const request = requestBudget(options);
  const selected = new Map<string, WorkloadInstance>();
  for (const instance of instances) {
    if (instance.environment !== options.environment) {
      throw new KubernetesError("Compute instance belongs to a different environment", { kind: "invalid_argument" });
    }
    selected.set(JSON.stringify([instance.namespace, instance.pod, instance.uid]), instance);
  }
  const pods: PodCompute[] = [], metrics: PodComputeUsage[] = [];
  const podIssues: ComputeIssue[] = [], metricIssues: ComputeIssue[] = [];
  if (selected.size > options.maxPods) podIssues.push(gap("pods", "limit_exceeded", `Collection limited to ${options.maxPods} Pods`));
  for (const instance of [...selected.values()].slice(0, options.maxPods)) {
    const source = `pods/${instance.namespace}/${instance.pod}`;
    let pod: PodCompute;
    try {
      pod = podCompute(await access.get({ apiVersion: "v1", resource: "pods", namespace: instance.namespace, name: instance.pod }, request()));
      if (pod.uid !== instance.uid || pod.name !== instance.pod || pod.namespace !== instance.namespace) {
        throw new KubernetesError("Pod identity changed after discovery", { kind: "invalid_response" });
      }
      pods.push(pod);
    } catch (error) {
      options.signal?.throwIfAborted(); podIssues.push(issue(source, error));
      metricIssues.push(gap(source, "operation_failed", "Metrics not collected because Pod identity could not be verified")); continue;
    }
    if (pod.phase === "Succeeded" || pod.phase === "Failed") continue;
    try {
      const raw = await access.get({ apiVersion: "metrics.k8s.io/v1beta1", resource: "pods", namespace: pod.namespace, name: pod.name }, request());
      if (raw.metadata?.name !== pod.name || raw.metadata?.namespace !== pod.namespace || !Array.isArray(raw.containers)) {
        throw new KubernetesError("Pod metrics lacks identity or containers", { kind: "invalid_response" });
      }
      const names = raw.containers.map(c => requiredString(object(c).name, "metrics container name"));
      if (new Set(names).size !== names.length || pod.containers.some(name => !names.includes(name))) {
        throw new KubernetesError("Pod metrics does not cover its containers", { kind: "invalid_response" });
      }
      const values = raw.containers.map(c => quantities(object(c).usage, true));
      if (!values.length) throw new KubernetesError("Empty Pod metrics", { kind: "invalid_response" });
      const measured = { ...usage({ ...raw, usage: { cpu: String(sum(values).cpu), memory: String(sum(values).memory) } }),
        namespace: pod.namespace, name: pod.name, uid: pod.uid };
      // Metrics API may omit Pod UID. Timestamp prevents joining a previous incarnation's stale sample.
      if ((raw.metadata.uid && raw.metadata.uid !== pod.uid)
        || (pod.createdAt && Date.parse(measured.timestamp) < Date.parse(pod.createdAt))) {
        throw new KubernetesError("Pod metrics precedes current Pod incarnation", { kind: "invalid_response" });
      }
      metrics.push(measured);
    } catch (error) { options.signal?.throwIfAborted(); metricIssues.push(issue(`metrics/${source}`, error)); }
  }
  if (selected.size > options.maxPods) metricIssues.push(gap("metrics/pods", "limit_exceeded", "Metrics limited to collected Pods"));
  options.signal?.throwIfAborted();
  const podRead = finish(pods, podIssues, pods.length > 0 || !selected.size);
  const metricRead = finish(metrics, metricIssues, metrics.length > 0 || (!podIssues.length && pods.every(p => p.phase === "Succeeded" || p.phase === "Failed")));
  return { environment: options.environment, observedAt: new Date().toISOString(), scope: "instances",
    workloads: instances.map(({ namespace, workload, pod, uid }) => ({ namespace, workload, pod, uid })),
    pods: podRead, usage: metricRead, summary: {
      pods: podRead.status === "unavailable" ? undefined : summarizePods(pods),
      usage: metricRead.status === "unavailable" ? undefined : sum(metrics),
    } };
}
