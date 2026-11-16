import {test, expect} from "bun:test";
import {readFileSync} from "node:fs";
import {Operation, Service, graphFromServices, selectProbes, repositoryId, type MetricQuery} from "../src/index.js";
const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/common/operations.json",import.meta.url),"utf8")) as {operations: {service: string; name: string; calls: string[]; mayCalls: string[]; metrics: Record<string, MetricQuery>}[]};
const services = fixture.operations.map((item) => new Service({name:item.service,
  environment:{name:"test"}, component:{name:"app",repository:{forge:{name:"github"},path:"example/app",id(){return repositoryId(this);}}},workloads:[],
  operations:[new Operation({name:item.name,serviceName:item.service,calls:item.calls,mayCalls:item.mayCalls,
    probes:Object.keys(item.metrics).length ? [{type:"prometheus",metrics:item.metrics}] : []})]}));
test("service operations assemble shared identity, cyclic topology and independent probes",()=>{
  const graph=graphFromServices(services,{source:"fixture",version:"1"});
  expect(graph.nodes).toHaveLength(4); expect(graph.calls).toHaveLength(2);
  expect(graph.reachableFrom("operation:front/chat").map(n=>n.id)).toContain("operation:worker/run");
  expect(graph.subgraph(["operation:front/chat"]).nodes).toHaveLength(2);
  expect(graph.toJSON().nodes[1]!.properties).toBeUndefined();
  expect(selectProbes(services[0].operations![0].probes!,"prometheus")).toHaveLength(1);
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
  const p={type:"prometheus",datasourceKind:"prometheus",metrics};
  expect(selectProbes([p,{...p,datasourceKind:"victoriametrics"}],"prometheus")).toEqual([p]);
  expect(()=>selectProbes([p],"victoriametrics")).toThrow("exactly one");
  expect(()=>selectProbes([p,{...p,datasourceKind:undefined}],"prometheus")).toThrow("exactly one");
});
