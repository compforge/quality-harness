import { expect, test } from "bun:test";
import { runInNewContext } from "node:vm";
import { strFromU8 } from "fflate";
import { TraceHarness, genAiSpecs, normalizeJaegerSpans } from "../src";
import { ARCHIVE_SCRIPT } from "../src/view/archive";
import { archiveEntries } from "./archive-fixture";

test("node and span details remain complete outside the lightweight tree index", () => {
  const body = "unabridged detail\n".repeat(100000);
  const h = new TraceHarness({ specs: genAiSpecs() });
  const c = h.assemble(normalizeJaegerSpans([{
    traceID: "trace", spanID: "span", operationName: "request", startTime: 1000000, duration: 5000,
    tags: [{ key: "large", value: body }], process: { serviceName: "fixture" },
  }]));
  const html = h.renderInteractive(c);
  const entries = archiveEntries(html);
  const indexText = strFromU8(entries["index.json"]!);
  expect(indexText.length).toBeLessThan(10000);
  expect(indexText).not.toContain("unabridged detail");
  const index = JSON.parse(indexText);
  expect(JSON.parse(strFromU8(entries[index.spans.span.payload]!)).attrs.large.value).toBe(body);
  const embedded = html.match(/<template id="trace-archive">([^<]+)<\/template>/)![1]!;
  const result = runInNewContext(ARCHIVE_SCRIPT + `
    const {trees:TREES,spans:SPANS}=readTraceEntry('index.json');
    const opened=[];const inflate=fflate.unzipSync;
    fflate.unzipSync=(bytes,options)=>inflate(bytes,{filter:entry=>{const accepted=options.filter(entry);if(accepted)opened.push(entry.name);return accepted;}});
    const span=loadSpan('span');({span,opened});`, {
    Uint8Array, TextDecoder, TextEncoder, atob,
    document: { getElementById: () => ({ content: { textContent: embedded }, remove() {} }) },
  });
  expect(result.span.attrs.large.value).toBe(body);
  expect([...result.opened]).toEqual([index.spans.span.payload]);
  expect(html.length).toBeLessThan(body.length / 4);
});
