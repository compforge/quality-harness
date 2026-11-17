import {expect,test} from "bun:test";
import {bindPrometheusProbe} from "../src/metricdb";
test("scope substitution preserves label string boundaries and requires explicit values",()=>{
 const probe={type:"prometheus",metrics:{qps:{unit:"rps",expression:'sum(rate(requests{namespace={{namespace}}}[$window]))'}}};
 const bound=bindPrometheusProbe(probe,{namespace:'a"b'});
 expect(bound.metrics.qps!.expression).toContain('namespace="a\\"b"');
 expect(bound.metrics.qps!.expression).toContain("$window");
 expect(()=>bindPrometheusProbe(probe,{})).toThrow("Missing metric scope");
});
