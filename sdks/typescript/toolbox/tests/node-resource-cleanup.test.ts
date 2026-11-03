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

for (const mode of ["ready", "exit", "timeout", "ignore-term"]) {
  test(`port-forward ${mode} releases its deadline and lets Node exit`, () => {
    const root = mkdtempSync(join(tmpdir(), "toolbox-forward-cleanup-"));
    try {
      // This subprocess never talks to Kubernetes; the timeout bounds cleanup even if the test fails.
      writeFileSync(join(root, "kubectl"), `#!/usr/bin/env node
if (${JSON.stringify(mode)} === "exit") { console.error("fixture unavailable"); process.exit(1); }
if (${JSON.stringify(mode)} === "ready") console.log("Forwarding from 127.0.0.1:12345 -> 9200");
if (${JSON.stringify(mode)} === "ignore-term") {
  process.on("SIGTERM", () => {});
  console.error("fixture forwarding stalled");
  setInterval(() => {}, 10000);
} else setTimeout(() => process.exit(0), 1000);
`, { mode: 0o700 });
      const entry = new URL("../dist/kubernetes/port-forward.js", import.meta.url).href;
      const result = spawnSync("node", ["--input-type=module", "-e", `
        import assert from 'node:assert/strict';
        import { startPortForward } from ${JSON.stringify(entry)};
        const mode = ${JSON.stringify(mode)};
        const diagnostics = [];
        const result = await startPortForward({onDiagnostic: line => diagnostics.push(line), service:'fixture',remotePort:9200,timeoutMs:mode === 'ignore-term' ? 500 : mode === 'timeout' ? 200 : 10000});
        assert.equal(result.ok, mode === 'ready');
        if (mode === 'exit') assert.match(result.reason, /fixture unavailable/);
        if (mode === 'ignore-term') assert.match(result.reason, /fixture forwarding stalled/);
        assert.match(diagnostics.join('\\n'), /port-forward start/);
        result.value?.stop();
        console.log('stopped');
      `], { encoding: "utf8", timeout: 3000, env: { ...process.env, PATH: `${root}:${process.env.PATH}` } });
      expect(result.stdout, result.stderr).toContain("stopped");
      expect(result.error).toBeUndefined();
      expect(result.stderr).toBe("");
      expect(result.status).toBe(0);
    } finally { rmSync(root, { recursive: true, force: true }); }
  }, 5000);
}

for (const mode of ["http", "tls", "proxy", "rotation", "active-rotation"]) {
  test(`native Kubernetes ${mode} reuses a physical connection and releases it on root disposal in Node`, () => {
    const result = spawnSync("node", [fileURLToPath(new URL("./fixtures/kubernetes-resource-reuse.mjs", import.meta.url)), mode],
      { encoding: "utf8", timeout: 5000 });
    expect(result.error).toBeUndefined();
    expect(result.status, result.stderr).toBe(0);
    expect(JSON.parse(result.stdout)).toEqual({ requests: 3, connections: mode.includes("rotation") ? 2 : 1, disposed: true, tls: mode === "tls" });
  }, 6000);
}

for (const runtime of ["node", "bun"]) for (const route of ["direct", "proxy", "proxy-name"]) test(`native Kubernetes ${runtime} ${route} exec authenticates with TLS and releases streams on completion, timeout and disposal`, () => {
  const result = spawnSync("node", [fileURLToPath(new URL("./fixtures/kubernetes-exec.mjs", import.meta.url)), runtime, route],
    { encoding: "utf8", timeout: 5000 });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  expect(result.stdout.trim()).toBe("disposed");
}, 6000);


for (const ready of [false, true]) test(`port-forward cancellation stops the actual child (ready=${ready})`, () => {
  const root = mkdtempSync(join(tmpdir(), "toolbox-forward-cancel-"));
  try {
    writeFileSync(join(root, "kubectl"), `#!/usr/bin/env node
process.on('SIGTERM', () => {});
${ready ? "console.log('Forwarding from 127.0.0.1:12345 -> 9200');" : ""}
setInterval(() => {}, 10000);
`, { mode: 0o700 });
    const entry = new URL("../dist/kubernetes/port-forward.js", import.meta.url).href;
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { startPortForward } from ${JSON.stringify(entry)};
      const controller = new AbortController();
      const reason = new Error('caller interrupted');
      const logs = [];
      const opts = { service: 'fixture', remotePort: 9200, timeoutMs: 10000,
        signal: controller.signal, onDiagnostic: line => logs.push(line) };
      if (${ready}) {
        const forward = await startPortForward(opts);
        assert.equal(forward.ok, true);
        controller.abort(reason);
        forward.value.stop();
      } else {
        const timer = setTimeout(() => controller.abort(reason), 500);
        await assert.rejects(startPortForward(opts), error => error === reason);
        clearTimeout(timer);
      }
      assert.match(logs.join('\\n'), /port-forward cancelled/);
      await assert.rejects(startPortForward(opts), error => error === reason);
      console.log('cancelled');
    `], { encoding: "utf8", timeout: 3000, env: { ...process.env, PATH: `${root}:${process.env.PATH}` } });
    expect(result.stdout, result.stderr).toContain("cancelled");
    expect(result.error).toBeUndefined();
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
}, 5000);
