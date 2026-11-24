import { expect, test } from "bun:test";
import { findPrometheusCollectors, findVMAgentCollectors, MetricDBClient, MetricDBError } from "../src/metricdb";
import type { Resource, ResourceAccess } from "../src/kubernetes/resources";

const namespace = "monitoring";
const queryURL = "http://192.0.2.1:8481/select/0/prometheus";

function service(name: string, role: string, clusterIP: string, port: number): Resource {
  return {
    metadata:{name, namespace, labels:{app:role, "app.kubernetes.io/name":"vm-stack"}},
    spec:{clusterIP, selector:{app:role}, ports:[{name:"http", port}]},
  };
}

function prometheus(name: string, clusterIP: string): Resource {
  return {
    metadata:{name, namespace, labels:{"app.kubernetes.io/name":"prometheus", "app.kubernetes.io/component":"server"}},
    spec:{clusterIP, ports:[{name:"http",port:80}]},
  };
}

function deployment(role: string, args: string[]): Resource {
  return {spec:{template:{metadata:{labels:{app:role}}, spec:{containers:[{args}]}}}};
}

function fixture(insertArgs: string[]) {
  const services = [
    service("stack-vmselect","vmselect","192.0.2.1",8481),
    service("stack-vminsert","vminsert","192.0.2.2",8480),
    prometheus("prometheus-server","192.0.2.3"),
    prometheus("other-prometheus","192.0.2.4"),
    prometheus("third-prometheus","192.0.2.5"),
  ];
  const deployments = [
    deployment("vmselect",["--storageNode=storage-0.storage.monitoring.svc:8401","--storageNode=storage-1.storage.monitoring.svc:8401"]),
    deployment("vminsert",insertArgs),
  ];
  const access: ResourceAccess = {
    async get() { throw new Error("unexpected get"); },
    async list(query) {
      if (query.resource === "services") return {items:services};
      expect(query).toMatchObject({apiVersion:"apps/v1",resource:"deployments",namespace});
      return {items:deployments};
    },
  };
  return access;
}

const writeStorage = ["-storageNode=storage-1.storage.monitoring.svc:8400,storage-0.storage.monitoring.svc:8400"];

test("identifies the Operator-managed VMAgent variant", async () => {
  const operator = "operator.victoriametrics.com/v1beta1";
  const owner = (kind: string, uid: string) => [{apiVersion:operator,kind,uid,controller:true}];
  const byRole: Readonly<Record<string, Resource>> = {
    vmselect:{metadata:{name:"stack-vmselect",namespace,ownerReferences:owner("VMCluster","stack")},
      spec:{clusterIP:"192.0.2.1",ports:[{name:"http",port:8481}]}},
    vminsert:{metadata:{name:"stack-vminsert",namespace,ownerReferences:owner("VMCluster","stack")},
      spec:{ports:[{name:"http",port:8480}]}},
    vmagent:{metadata:{name:"scraper",namespace,ownerReferences:owner("VMAgent","writer")},
      spec:{clusterIP:"192.0.2.6",ports:[{name:"http",port:8429}]}},
  };
  const access: ResourceAccess = {
    async get() { throw new Error("unexpected get"); },
    async list({resource,labelSelector}) {
      if (resource === "vmagents") return {items:[{
        metadata:{name:"writer",namespace,uid:"writer"},
        spec:{remoteWrite:[{url:"http://stack-vminsert:8480/insert/0/prometheus/api/v1/write"}]},
      }]};
      const role = labelSelector?.split("=")[1] ?? "";
      return {items:byRole[role] ? [byRole[role]!] : []};
    },
  };
  const collectors = await findVMAgentCollectors(access, queryURL, namespace);
  expect(collectors).toEqual([{kind:"vmagent",namespace,agent:"writer",service:"scraper",url:"http://192.0.2.6:8429"}]);
});

test("finds the Prometheus writer of a Helm VM cluster using loaded config and matching storage membership", async () => {
  const requests: string[] = [];
  const client = new MetricDBClient({
    async request(url, options) {
      requests.push(url);
      expect(options?.headers?.Authorization).toBeUndefined();
      const path = new URL(url).pathname;
      expect(path).toBe("/api/v1/status/config");
      const writes: Readonly<Record<string, string>> = {
        "192.0.2.3":"http://stack-vminsert:8480/insert/0/prometheus",
        "192.0.2.4":"http://stack-vminsert:8480/insert/1/prometheus/api/v1/write",
        "192.0.2.5":"http://external.example:8480/insert/0/prometheus/api/v1/write",
      };
      const yaml = `remote_write:\n- url: ${writes[new URL(url).hostname]}\nscrape_configs: []`;
      return {status:200, headers:{}, body:new TextEncoder().encode(JSON.stringify({status:"success",data:{yaml}}))};
    },
  }, {url:queryURL, username:"query", password:"secret"});

  const discover = (access: ResourceAccess) =>
    findPrometheusCollectors(access, queryURL, namespace, url => client.forCollector({url}));
  expect(await discover(fixture(writeStorage))).toEqual([{
    kind:"prometheus", namespace, service:"prometheus-server", url:"http://192.0.2.3",
    vmselect:"stack-vmselect", vminsert:"stack-vminsert", tenant:"0",
  }]);
  expect(requests).toHaveLength(3);
  requests.length = 0;
  expect(await discover(fixture(["-storageNode=storage-0.storage.monitoring.svc:8400"]))).toEqual([]);
  expect(requests).toHaveLength(0);
});

test("propagates incomplete verification instead of reporting a unique Prometheus collector", async () => {
  const denied = new MetricDBError("Config access denied", {kind:"permission_denied", code:403});
  await expect(findPrometheusCollectors(fixture(writeStorage), queryURL, namespace, url => ({
    async getScrapeConfig(_signal, onYaml) {
      if (url === "http://192.0.2.4") throw denied;
      onYaml?.("remote_write: [{url: 'http://stack-vminsert:8480/insert/0/prometheus/api/v1/write'}]");
      return [];
    },
  }))).rejects.toBe(denied);
});
