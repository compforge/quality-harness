import { expect, test } from "bun:test";
import { cpSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

test("published lifecycle and transport entries run in Node without protocol drivers", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { ClientManager } from '@compforge/harness-toolbox';
      import { ClientManager as Subpath } from '@compforge/harness-toolbox/client-manager';
      import { DirectTransport } from '@compforge/harness-toolbox/transport';
      assert.equal(ClientManager, Subpath);
      let created = 0, closed = 0;
      const source = { key: 'test', createClient: () => {
        created++; return { initialize: async () => {}, dispose: async () => { closed++; } };
      } };
      const manager = new ClientManager();
      const [a,b] = await Promise.all([manager.get(source), manager.get(source)]);
      assert.equal(a,b); assert.equal(created,1);
      await manager.dispose(); await manager.dispose(); assert.equal(closed,1);
      assert.deepEqual(await new DirectTransport().connect({host:'localhost',port:1}), {host:'localhost',port:1});
    `], { cwd: root, encoding: "utf8" });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
