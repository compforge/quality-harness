import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

for (const mode of ["close", "query-error", "readonly-error"]) {
  test(`MySQL ${mode} lets Node exit when the peer keeps its TCP sending side open`, () => {
    const result = spawnSync("node", [fileURLToPath(new URL("./fixtures/mysql-cleanup.mjs", import.meta.url)), mode],
      { encoding: "utf8", timeout: 3000 });
    expect(result.stdout).toContain("disposed");
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  }, 5000);
}

for (const mode of ["ready", "exit", "timeout"]) {
  test(`port-forward ${mode} releases its deadline and lets Node exit`, () => {
    const root = mkdtempSync(join(tmpdir(), "toolbox-forward-cleanup-"));
    try {
      // This subprocess never talks to Kubernetes; the timeout bounds cleanup even if the test fails.
      writeFileSync(join(root, "kubectl"), `#!/usr/bin/env node
if (${JSON.stringify(mode)} === "exit") { console.error("fixture unavailable"); process.exit(1); }
if (${JSON.stringify(mode)} === "ready") console.log("Forwarding from 127.0.0.1:12345 -> 9200");
setTimeout(() => process.exit(0), 1000);
`, { mode: 0o700 });
      const entry = new URL("../dist/kubernetes/port-forward.js", import.meta.url).href;
      const result = spawnSync("node", ["--input-type=module", "-e", `
        import assert from 'node:assert/strict';
        import { startPortForward } from ${JSON.stringify(entry)};
        const mode = ${JSON.stringify(mode)};
        const result = await startPortForward({service:'fixture',remotePort:9200,timeoutMs:mode === 'timeout' ? 100 : 10000});
        assert.equal(result.ok, mode === 'ready');
        if (mode === 'exit') assert.match(result.reason, /fixture unavailable/);
        result.value?.stop();
        console.log('stopped');
      `], { encoding: "utf8", timeout: 3000, env: { ...process.env, PATH: `${root}:${process.env.PATH}` } });
      expect(result.stdout).toContain("stopped");
      expect(result.error).toBeUndefined();
      expect(result.stderr).toBe("");
      expect(result.status).toBe(0);
    } finally { rmSync(root, { recursive: true, force: true }); }
  }, 5000);
}
