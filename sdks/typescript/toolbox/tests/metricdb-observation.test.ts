import {expect,test} from "bun:test";
import {bindMetricDBObservation, bindMetricDBObservations} from "../src/metricdb";
test("scope substitution preserves label string boundaries and requires explicit values",()=>{
 const probe={type:"metricdb",metrics:{qps:{unit:"rps",expression:'sum(rate(requests{namespace={{namespace}}}[$window]))'}}};
 const bound=bindMetricDBObservation(probe,{namespace:'a"b'});
 expect(bound.metrics.qps!.expression).toContain('namespace="a\\"b"');
 expect(bound.metrics.qps!.expression).toContain("$window");
 expect(()=>bindMetricDBObservation(probe,{})).toThrow("Missing metric scope");
});

test("metricdb capability accepts both backend kinds and rejects the old protocol type",()=>{
 const metrics={qps:{unit:"rps",expression:"requests"}};
 for(const kind of ["prometheus","victoriametrics"])
   expect(bindMetricDBObservation({type:"metricdb",kind,metrics},{})).toEqual({type:"metricdb",kind,metrics});
 expect(()=>bindMetricDBObservation({type:"prometheus",metrics},{})).toThrow("Unsupported observation type");
});

test("metricdb binds only its own declarations and rejects competing producers", () => {
  const measurement = {type: "metricdb", metrics: {depth: {unit: "count", expression: "queue_depth"}}};
  expect(bindMetricDBObservations([{...measurement, type: "logs"}, measurement], "prometheus")).toEqual([measurement]);
  expect(() => bindMetricDBObservations([measurement, measurement], "prometheus")).toThrow("exactly one");
});
