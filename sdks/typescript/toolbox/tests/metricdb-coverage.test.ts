import {expect, test} from "bun:test";
import {inspectMetricSamples, observationSampleInputs, summarizeScrapeTargets} from "../src/metricdb";

test("density checks use literal window inputs and keep zero series distinct from incomplete reads", async () => {
  const selector = 'http_total{path="/quote\\"/x",status=~"2.."}';
  expect(observationSampleInputs(`sum(rate(${selector}[$window])) / sum(rate(${selector}[$window]))`)).toEqual({selectors:[selector],windowed:true});
  expect(observationSampleInputs('label_replace(up,"x","name[$window]","y","z") # foo[$window]')).toEqual({selectors:[],windowed:false});
  expect(observationSampleInputs('rate(foo[$window] offset 5m) + max_over_time((a+b)[$window:1m])')).toEqual({selectors:[],windowed:false});
  expect(observationSampleInputs(selector)).toEqual({selectors:[selector],windowed:false});
  const read = (result: unknown, partial = false) => ({query:async (expression: string) => {
    expect(expression).toBe(`count_values("samples", count_over_time(${selector}[60000ms]))`);
    return {data:{resultType:"vector",result}, isPartial:partial, warnings:[],infos:[]};
  }});
  expect(await inspectMetricSamples(read([]),selector,10,60)).toEqual({series:0});
  expect(await inspectMetricSamples(read([{metric:{samples:"1"},value:[10,"3"]},{metric:{samples:"4"},value:[10,"2"]}]),selector,10,60))
    .toEqual({series:5,minSamples:1,maxSamples:4});
  await expect(inspectMetricSamples(read([],true),selector,10,60)).rejects.toThrow("incomplete");
});

test("group targets with reported and configured scrape intervals separately", () => {
  const targets = [{state:"active",health:"up",scrapePool:"api",scrapeInterval:"15s"},
    {state:"active",health:"down",scrapePool:"api"}] as const;
  expect(summarizeScrapeTargets(targets,[{jobName:"api",scrapeInterval:"1m"}])).toMatchObject([
    {count:1,intervalSeconds:15,intervalSource:"target"}, {count:1,intervalSeconds:60,intervalSource:"config"},
  ]);
});
