import { expect, test } from "bun:test";
import { HttpOperation } from "@compforge/harness-common";
import { operationMetricProbes } from "../src/observe/operations";

test("complete Operation queries need only backend and realized windows, not host label parameters", () => {
  const operation = new HttpOperation({serviceName:"api",name:"read",method:"GET",path:"/items",
    probes: ["prometheus","victoriametrics"].map(datasourceKind => ({type:"prometheus",datasourceKind,
      metrics:{qps:{unit:"rps",expression:`sum(rate(requests{job="api",path="/items"}[$window]))`}}}))});
  const [probe] = operationMetricProbes([operation],"victoriametrics");
  expect(probe!.summaries).toHaveLength(2);
  expect(probe!.target).toBe(operation.id());
  expect(probe!.summaries.every(query => query.promql.includes('job="api"'))).toBe(true);
  expect(() => operationMetricProbes([operation],"unknown")).toThrow("exactly one producer");
  const incomplete = new HttpOperation({serviceName:"api",name:"missing",method:"GET",path:"/items",
    probes:[{type:"prometheus",metrics:{qps:{unit:"rps",expression:"requests{namespace={{namespace}}}"}}}]});
  expect(() => operationMetricProbes([incomplete],"prometheus")).toThrow("Missing metric scope parameter");
});
