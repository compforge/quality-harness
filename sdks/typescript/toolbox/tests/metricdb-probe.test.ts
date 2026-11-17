import {expect,test} from "bun:test";
import {bindMetricDBProbe} from "../src/metricdb";
test("scope substitution preserves label string boundaries and requires explicit values",()=>{
 const probe={type:"metricdb",metrics:{qps:{unit:"rps",expression:'sum(rate(requests{namespace={{namespace}}}[$window]))'}}};
 const bound=bindMetricDBProbe(probe,{namespace:'a"b'});
 expect(bound.metrics.qps!.expression).toContain('namespace="a\\"b"');
 expect(bound.metrics.qps!.expression).toContain("$window");
 expect(()=>bindMetricDBProbe(probe,{})).toThrow("Missing metric scope");
});

test("metricdb capability accepts both backend kinds and rejects the old protocol type",()=>{
 const metrics={qps:{unit:"rps",expression:"requests"}};
 for(const kind of ["prometheus","victoriametrics"])
   expect(bindMetricDBProbe({type:"metricdb",kind,metrics},{})).toEqual({type:"metricdb",kind,metrics});
 expect(()=>bindMetricDBProbe({type:"prometheus",metrics},{})).toThrow("Unsupported query probe type");
});
