import { MetricDBError } from "../../errors";
import type { MetricDBReader } from "../model";
import { isIP } from "node:net";
import type { Resource, ResourceAccess, ResourceListQuery } from "../../kubernetes/resources";
import { serviceIdentity } from "../../kubernetes/service";

const apiVersion = "operator.victoriametrics.com/v1beta1";

export interface VMAgentCollector {
  namespace: string;
  agent: string;
  service: string;
  url: string;
}

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

function collectorFor(agent: Resource, service: Resource): VMAgentCollector | undefined {
  const namespace = agent.metadata?.namespace, name = agent.metadata?.name, serviceName = service.metadata?.name;
  const spec = service.spec;
  if (!namespace || !name || !serviceName || !spec || !Array.isArray(spec.ports)) return;
  const port = spec.ports.find(item => item.name === "http")?.port;
  if (!Number.isInteger(port)) return;
  const args = agent.spec?.extraArgs as Record<string, string> | undefined;
  const tls = args?.tls === "true";
  // TLS uses the Service name for certificate verification; direct HTTP can use a reachable ClusterIP.
  const ip = typeof spec.clusterIP === "string" && isIP(spec.clusterIP) ? spec.clusterIP : undefined;
  const host = tls || !ip ? `${serviceName}.${namespace}.svc` : isIP(ip) === 6 ? `[${ip}]` : ip;
  const url = new URL(`${tls ? "https" : "http"}://${host}:${port}`);
  url.pathname = args?.["http.pathPrefix"] ?? "/";
  return {namespace, agent:name, service:serviceName, url:url.href.replace(/\/$/, "")};
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

/** Stored self-monitoring labels are diagnostic hints, with no guarantee of reachability or scrape ownership. */
export async function listVMAgentInstances(reader: MetricDBReader, time: number, signal?: AbortSignal): Promise<string[]> {
  const result = await reader.query('topk(8, count by (instance) (vmagent_remotewrite_bytes_sent_total{instance!=""}))', time, signal);
  const items = result.data.result;
  if (result.data.resultType !== "vector" || !Array.isArray(items)) return [];
  return items.flatMap(item => typeof item?.metric?.instance === "string" ? [item.metric.instance] : []);
}
