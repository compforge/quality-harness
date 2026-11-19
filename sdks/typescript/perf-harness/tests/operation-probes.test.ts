import { expect, test } from "bun:test";
import { HttpOperation } from "@compforge/harness-common";
import { operationMetricProbes } from "../src/observe/operations";

test("complete Operation queries need only backend and realized windows, not host label parameters", () => {
  const operation = new HttpOperation({serviceName:"api",name:"read",method:"GET",path:"/items",
    observes: ["prometheus","victoriametrics"].map(kind => ({type:"metricdb",kind,
      metrics:{qps:{unit:"rps",expression:`sum(rate(requests{job="api",path="/items"}[$window]))`}}}))});
  const [probe] = operationMetricProbes([operation],"victoriametrics");
  expect(probe!.summaries).toHaveLength(2);
  expect(probe!.target).toBe(operation.id());
  expect(probe!.summaries.every(query => query.promql.includes('job="api"'))).toBe(true);
  expect(() => operationMetricProbes([operation],"unknown")).toThrow("exactly one producer");
  const incomplete = new HttpOperation({serviceName:"api",name:"missing",method:"GET",path:"/items",
    observes:[{type:"metricdb",metrics:{qps:{unit:"rps",expression:"requests{namespace={{namespace}}}"}}}]});
  expect(() => operationMetricProbes([incomplete],"prometheus")).toThrow("Missing metric scope parameter");
});

test("Operation metric categories select canonical RED slots independently of registered names", () => {
  const operation = new HttpOperation({serviceName:"api", name:"read", method:"GET",path:"/items",
    observes:[{type:"metricdb", metrics:{
      ingress:{category:"red:qps", unit:"rps", semantics:"entered", expression:"ingress"},
      qps:{unit:"bytes", expression:"custom"},
      first_chunk:{category:"red:other", unit:"s", expression:"first_chunk"},
      memory:{category:"resource:mem", unit:"bytes", expression:"memory"},
    }}]});
  const [probe] = operationMetricProbes([operation], "prometheus");
  expect(probe!.metrics.qps).toBe(operation.id()+".ingress");
  expect(probe!.qps_semantics).toBe("entered");
  expect(Object.values(probe!.metrics)).toContain(operation.id()+".qps");
  expect(probe!.summaries.find(q => q.name.endsWith(".memory"))?.category).toBe("resource:mem");
});
