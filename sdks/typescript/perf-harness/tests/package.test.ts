import { expect, test } from "bun:test";
import { cpSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

test("published ESM renders under Node outside the checkout without runtime assets or Python", () => {
  const directory=mkdtempSync(join(tmpdir(),"perf-node-package-"));
  const root=fileURLToPath(new URL("../",import.meta.url));
  try {
    cpSync(join(root,"dist"),join(directory,"dist"),{recursive:true});
    writeFileSync(join(directory,"package.json"),'{"type":"module"}');
    symlinkSync(join(root,"node_modules"),join(directory,"node_modules"));
    cpSync(fileURLToPath(new URL("../../../../conformance/perf/fixtures/operation-metrics/",import.meta.url)),join(directory,"run"),{recursive:true});
    const result=spawnSync("node",["--input-type=module","-e",`
      import {loadRun,writePerfReport} from "./dist/index.js";
      import {Operation} from "@compforge/harness-common";
      const run=loadRun("./run");
      if (!(run.executions[0].operation_runs[0].operation instanceof Operation)) throw new Error("Common class identity split");
      writePerfReport(run,"./report");
    `],{cwd:directory,encoding:"utf8",timeout:15000});
    expect(result.status,result.stderr).toBe(0);
    expect(readFileSync(join(directory,"report/report.html"),"utf8")).toContain("rkGraph");
    expect(JSON.parse(readFileSync(join(directory,"report/callgraph.json"),"utf8")).schema_version).toBe(2);
  } finally {rmSync(directory,{recursive:true,force:true});}
});
