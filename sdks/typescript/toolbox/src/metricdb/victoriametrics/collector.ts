import { MetricDBError } from "../../errors";
import type { MetricDBReader, ScrapeReader } from "../model";
import { isIP } from "node:net";
import type { Resource, ResourceAccess, ResourceListQuery } from "../../kubernetes/resources";
import { serviceIdentity } from "../../kubernetes/service";
import { remoteWriteURLs } from "../scrape";

const apiVersion = "operator.victoriametrics.com/v1beta1";

/** A scrape endpoint identified by the metric store's write topology. */
export type MetricCollector = {
  namespace: string;
  service: string;
  url: string;
} & (
  | { kind: "vmagent"; agent: string }
  | { kind: "prometheus"; vmselect: string; vminsert: string; tenant: string }
);

export type VMAgentCollector = Extract<MetricCollector, { kind: "vmagent" }>;
export type PrometheusCollector = Extract<MetricCollector, { kind: "prometheus" }>;

/** Tenant-qualified vmselect URLs identify the read side of a VM cluster. */
export function vmSelectTenant(url: string): string | undefined {
  if (!URL.canParse(url)) throw new MetricDBError("MetricDB requires a valid absolute URL", {kind: "invalid_argument"});
  const match = /^\/select\/(\d+(?::\d+)?)\/prometheus\/?$/.exec(new URL(url).pathname);
  return match?.[1]?.replace(/:0$/, "");
}

async function listResources(access: ResourceAccess, query: ResourceListQuery, signal?: AbortSignal) {
  const items: Resource[] = [];
  let cursor: string | undefined;
  do {
    signal?.throwIfAborted();
    const page = await access.list({...query, limit: 100, continue: cursor}, {signal});
    items.push(...page.items);
    cursor = page.metadata?.continue;
  } while (cursor);
  return items;
}

function ownerUid(service: Resource, kind: "VMCluster" | "VMAgent"): string | undefined {
  const owners = service.metadata?.ownerReferences;
  if (!Array.isArray(owners)) return;
  return owners.find(owner => owner?.apiVersion === apiVersion && owner.kind === kind && owner.controller === true)?.uid;
}

function serves(service: Resource, url: URL, defaultNamespace: string): boolean {
  const host = url.hostname.replace(/^\[|\]$/g, "");
  // Full DNS names outside the standard cluster domain require an explicit route mapping.
  const identity = host.split(".").length <= 3 || host.endsWith(".svc.cluster.local")
    ? serviceIdentity(host, defaultNamespace) : undefined;
  const spec = service.spec;
  if (!spec || !Array.isArray(spec.ports)) return false;
  const port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
  if (!spec.ports.some(item => item.port === port)) return false;
  return isIP(host) ? spec.clusterIP === host || (Array.isArray(spec.clusterIPs) && spec.clusterIPs.includes(host))
    : identity !== undefined && identity.name === service.metadata?.name && identity.namespace === service.metadata?.namespace;
}

function writesTo(agent: Resource, inserts: Resource[], tenant: string): boolean {
  const writes = agent.spec?.remoteWrite;
  if (!Array.isArray(writes)) return false;
  return writes.some(write => {
    if (typeof write?.url !== "string" || !URL.canParse(write.url)) return false;
    const url = new URL(write.url);
    const match = /^\/insert\/(\d+(?::\d+)?)\/prometheus\/api\/v1\/write\/?$/.exec(url.pathname);
    return ["https:", "http:"].includes(url.protocol) && match?.[1]?.replace(/:0$/, "") === tenant
      && inserts.some(insert => serves(insert, url, agent.metadata!.namespace!));
  });
}

function collectorURL(service: Resource, tls: boolean, path: string): string | undefined {
  const { namespace, name } = service.metadata ?? {};
  const spec = service.spec;
  if (!namespace || !name || !spec || !Array.isArray(spec.ports)) return;
  const port = spec.ports.find(item => item.name === "http")?.port;
  if (!Number.isInteger(port) || port < 1 || port > 65535) return;
  // TLS uses the Service name for certificate verification; direct HTTP can use a reachable ClusterIP.
  let host = `${name}.${namespace}.svc`;
  if (!tls && typeof spec.clusterIP === "string") {
    const family = isIP(spec.clusterIP);
    if (family === 4) host = spec.clusterIP;
    if (family === 6) host = `[${spec.clusterIP}]`;
  }
  const url = new URL(`${tls ? "https" : "http"}://${host}:${port}`);
  url.pathname = path;
  return url.href.replace(/\/$/, "");
}

function collectorFor(agent: Resource, service: Resource): VMAgentCollector | undefined {
  const { namespace, name } = agent.metadata ?? {};
  const serviceName = service.metadata?.name;
  if (!namespace || !name || !serviceName) return;
  const args = agent.spec?.extraArgs as Record<string, string> | undefined;
  const url = collectorURL(service, args?.tls === "true", args?.["http.pathPrefix"] ?? "/");
  if (url) return {kind:"vmagent", namespace, agent:name, service:serviceName, url};
}

/** @rule Require a shared VMCluster owner and the writer's tenant before returning an agent Service. */
export async function findVMAgentCollectors(access: ResourceAccess, queryUrl: string, namespace: string,
  signal?: AbortSignal): Promise<VMAgentCollector[]> {
  const tenant = vmSelectTenant(queryUrl);
  if (tenant === undefined) return [];
  const query = new URL(queryUrl);
  const servicesFor = (role: string, ns?: string) => listResources(access,
    {apiVersion:"v1", resource:"services", namespace:ns, labelSelector:"app.kubernetes.io/name=" + role}, signal);
  const selects = (await servicesFor("vmselect")).filter(service => serves(service, query, namespace));
  if (selects.length !== 1) return [];
  const select = selects[0]!;
  const uid = ownerUid(select, "VMCluster");
  if (!uid || !select.metadata?.namespace) return [];
  const inserts = (await servicesFor("vminsert", select.metadata.namespace)).filter(s => ownerUid(s, "VMCluster") === uid);
  if (!inserts.length) return [];
  const agents = (await listResources(access, {apiVersion, resource:"vmagents"}, signal))
    .filter(agent => agent.metadata?.namespace && agent.metadata.uid && writesTo(agent, inserts, tenant));
  if (!agents.length) return [];
  const services = await servicesFor("vmagent");
  const candidates: VMAgentCollector[] = [];
  for (const agent of agents) {
    for (const service of services) {
      if (service.metadata?.namespace !== agent.metadata?.namespace || ownerUid(service, "VMAgent") !== agent.metadata!.uid) continue;
      const candidate = collectorFor(agent, service);
      if (candidate) candidates.push(candidate);
    }
  }
  return candidates;
}

function hasRole(service: Resource, role: "vmselect" | "vminsert"): boolean {
  const labels = service.metadata?.labels as Record<string, string> | undefined;
  return labels?.app === role || labels?.["app.kubernetes.io/name"] === role
    || labels?.["app.kubernetes.io/component"] === role;
}

function storageHosts(service: Resource, deployments: readonly Resource[], port: string): string[] {
  const selector = Object.entries(service.spec?.selector ?? {});
  if (!selector.length) return [];
  const templates = deployments.map(deployment => deployment.spec?.template as {
    metadata?: { labels?: Record<string, string> };
    spec?: { containers?: { args?: string[] }[] };
  } | undefined).filter(template => selector.every(([key, value]) => template?.metadata?.labels?.[key] === value));
  if (templates.length !== 1) return [];
  const hosts = new Set<string>();
  for (const container of templates[0]?.spec?.containers ?? []) {
    const args = container.args ?? [];
    for (const [i, arg] of args.entries()) {
      const flag = /^--?storageNode(?:=(.*))?$/.exec(arg);
      if (!flag) continue;
      const value = flag[1] ?? args[i + 1];
      if (!value) return [];
      for (const address of value.split(",")) {
        const endpoint = `http://${address.trim()}`;
        if (!URL.canParse(endpoint)) return [];
        const url = new URL(endpoint);
        if (url.port !== port || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return [];
        hosts.add(url.hostname.replace(/\.$/, ""));
      }
    }
  }
  return [...hosts].sort();
}

function prometheusWriteTenant(url: URL): string | undefined {
  if (url.protocol !== "http:" && url.protocol !== "https:") return;
  return /^\/insert\/(\d+(?::\d+)?)\/prometheus(?:\/api\/v1\/write)?\/?$/.exec(url.pathname)?.[1]?.replace(/:0$/, "");
}

/**
 * @spec Find Prometheus Services whose loaded configuration writes to the queried VM cluster and tenant.
 * @rule Kubernetes identity and storage membership establish the VM read/write pair. The caller verifies
 * active targets and decides whether to use a candidate. Collector readers must use their own authentication.
 */
export async function findPrometheusCollectors(access: ResourceAccess, queryUrl: string, namespace: string,
  readerFor: (url: string) => Pick<ScrapeReader, "getScrapeConfig">, signal?: AbortSignal): Promise<PrometheusCollector[]> {
  const tenant = vmSelectTenant(queryUrl);
  if (tenant === undefined) return [];
  const query = new URL(queryUrl);
  if (query.protocol !== "http:" && query.protocol !== "https:") return [];
  const services = await listResources(access, {apiVersion:"v1", resource:"services"}, signal);
  const selects = services.filter(service => hasRole(service, "vmselect") && serves(service, query, namespace));
  if (selects.length !== 1) return [];
  const select = selects[0]!;
  const vmNamespace = select.metadata?.namespace;
  const vmselect = select.metadata?.name;
  if (!vmNamespace || !vmselect) return [];

  const localServices = services.filter(service => service.metadata?.namespace === vmNamespace);
  const writeServices = localServices.filter(service => hasRole(service, "vminsert"));
  if (!writeServices.length) return [];
  const owner = ownerUid(select, "VMCluster");
  let inserts: Resource[];
  if (owner) {
    inserts = writeServices.filter(service => ownerUid(service, "VMCluster") === owner);
  } else {
    const deployments = await listResources(access, {apiVersion:"apps/v1", resource:"deployments", namespace:vmNamespace}, signal);
    const readHosts = storageHosts(select, deployments, "8401");
    if (!readHosts.length) return [];
    inserts = writeServices.filter(service => {
      const writeHosts = storageHosts(service, deployments, "8400");
      return writeHosts.length === readHosts.length && writeHosts.every((host, index) => host === readHosts[index]);
    });
  }
  if (!inserts.length) return [];

  const prometheus = localServices.flatMap(service => {
    const labels = service.metadata?.labels as Record<string, string> | undefined;
    if (labels?.["app.kubernetes.io/name"] !== "prometheus" || labels["app.kubernetes.io/component"] !== "server") return [];
    const url = collectorURL(service, false, "/");
    return url && service.metadata?.name ? [{ service: service.metadata.name, url }] : [];
  });
  // Bound config reads, since each distinct endpoint can own its own connection pool.
  if (prometheus.length > 8) throw new MetricDBError("Too many Prometheus Services to verify in the VM namespace", {kind:"operation_failed"});
  const candidates = await Promise.all(prometheus.map(async ({service, url}): Promise<PrometheusCollector | undefined> => {
    let yaml: string | undefined;
    signal?.throwIfAborted();
    await readerFor(url).getScrapeConfig(signal, config => { yaml = config; });
    if (yaml === undefined) throw new MetricDBError("Prometheus config YAML is unavailable", {kind:"invalid_response"});
    for (const destination of await remoteWriteURLs(yaml)) {
      if (!URL.canParse(destination)) continue;
      const endpoint = new URL(destination);
      if (prometheusWriteTenant(endpoint) !== tenant) continue;
      const matched = inserts.filter(insert => serves(insert, endpoint, vmNamespace));
      const vminsert = matched.length === 1 ? matched[0]?.metadata?.name : undefined;
      if (vminsert) return {kind:"prometheus", namespace:vmNamespace, service, url, vmselect, vminsert, tenant};
    }
  }));
  return candidates.filter(candidate => candidate !== undefined);
}

/** Stored self-monitoring labels are diagnostic hints, with no guarantee of reachability or scrape ownership. */
export async function listVMAgentInstances(reader: MetricDBReader, time: number, signal?: AbortSignal): Promise<string[]> {
  const result = await reader.query('topk(8, count by (instance) (vmagent_remotewrite_bytes_sent_total{instance!=""}))', time, signal);
  const items = result.data.result;
  if (result.data.resultType !== "vector" || !Array.isArray(items)) return [];
  return items.flatMap(item => typeof item?.metric?.instance === "string" ? [item.metric.instance] : []);
}
