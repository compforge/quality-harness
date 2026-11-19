import {test, expect} from "bun:test";
import {readFileSync} from "node:fs";
import {Operation, Service, graphFromServices, selectObservations, repositoryId, type MetricQuery} from "../src/index.js";
const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/common/operations.json",import.meta.url),"utf8")) as {operations: {service: string; name: string; calls: string[]; mayCalls: string[]; metrics: Record<string, MetricQuery>}[]};
const services = fixture.operations.map((item) => new Service({name:item.service,
  environment:{name:"test"}, component:{name:"app",repository:{forge:{name:"github"},path:"example/app",id(){return repositoryId(this);}}},workloads:[],
  operations:[new Operation({name:item.name,serviceName:item.service,calls:item.calls,mayCalls:item.mayCalls,
    observes:Object.keys(item.metrics).length ? [{type:"metricdb",metrics:item.metrics}] : []})]}));
test("service operations assemble shared identity, cyclic topology and independent observations",()=>{
  const graph=graphFromServices(services,{source:"fixture",version:"1"});
  expect(graph.nodes).toHaveLength(4); expect(graph.calls).toHaveLength(2);
  expect(graph.reachableFrom("operation:front/chat").map(n=>n.id)).toContain("operation:worker/run");
  expect(graph.subgraph(["operation:front/chat"]).nodes).toHaveLength(2);
  expect(graph.toJSON().nodes[1]!.properties).toBeUndefined();
  expect(selectObservations(services[0].operations![0].observes!,"prometheus")).toHaveLength(1);
});
test("catalog rejects dangling, conflicting, duplicate and wrong-owner declarations",()=>{
  const front=services[0];
  for(const patch of [{calls:["missing"]},{calls:["operation:worker/run"],mayCalls:["operation:worker/run"]},
    {calls:["operation:worker/run","operation:worker/run"]},{serviceName:"other"}]) {
    expect(()=>graphFromServices([new Service({...front,operations:[new Operation({...front.operations![0],...patch})]}),services[1]])).toThrow();
  }
});
test("backend selection rejects missing and ambiguous roles before executing producers",()=>{
  const metrics={qps:{unit:"rps",expression:"counter"}};
  const p={type:"metricdb",kind:"prometheus",metrics};
  expect(selectObservations([p,{...p,kind:"victoriametrics"}],"prometheus")).toEqual([p]);
  expect(()=>selectObservations([p],"victoriametrics")).toThrow("exactly one");
  expect(()=>selectObservations([p,{...p,kind:undefined}],"prometheus")).toThrow("exactly one");
});

test("kind is optional for universal queries and must be nonempty when specified",()=>{
 const probe={type:"metricdb",metrics:{qps:{unit:"rps",expression:"requests"}}};
 expect(selectObservations([probe],"victoriametrics")).toEqual([probe]);
 for(const kind of ["", " "]) expect(()=>selectObservations([{...probe,kind}],"prometheus")).toThrow("kind must be a nonempty string");
});

test("the same observation can be attached to the service, workload and operation", () => {
  const operation = services[0].operations![0];
  const observes = operation.observes!;
  const service = new Service({...services[0], observes, workloads: [{
    name: "web", platform: "kubernetes", location: {kind: "service", name: "web"}, observes,
  }]});
  expect(service.observes![0]).toBe(operation.observes![0]);
  expect(service.workloads[0]!.observes![0]).toBe(operation.observes![0]);
  expect(service.id()).toBe(services[0].id());
});
