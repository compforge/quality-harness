import { expect, test } from "bun:test";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

function installCommon(root: string): void {
  const source = dirname(dirname(fileURLToPath(import.meta.resolve("@compforge/harness-common"))));
  const target = join(root, "node_modules/@compforge/harness-common");
  mkdirSync(target, { recursive: true });
  cpSync(join(source, "dist"), join(target, "dist"), { recursive: true });
  cpSync(join(source, "package.json"), join(target, "package.json"));
}

test("published lifecycle and transport entries run in Node without protocol drivers", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    installCommon(root);
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { ClientManager } from '@compforge/harness-toolbox';
      import { ClientManager as Subpath } from '@compforge/harness-toolbox/client-manager';
      import { ClientManager as Common } from '@compforge/harness-common';
      import { DirectTransport } from '@compforge/harness-toolbox/transport';
      assert.equal(ClientManager, Subpath);
      assert.equal(ClientManager, Common);
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

test("published S3 entry performs bounded signed reads through a mapped route in Node", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-s3-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    installCommon(root);
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    const require = createRequire(import.meta.url);
    for (const dependency of ["@aws-sdk/client-s3", "@smithy/node-http-handler"]) {
      const link = join(root, "node_modules", dependency);
      mkdirSync(dirname(link), { recursive: true });
      symlinkSync(dirname(require.resolve(`${dependency}/package.json`)), link, "dir");
    }
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { createServer } from 'node:http';
      import { ClientManager } from '@compforge/harness-toolbox';
      import { S3DataSource } from '@compforge/harness-toolbox/s3';
      import { PortForwardTransport } from '@compforge/harness-toolbox/transport';
      let headers;
      const server = createServer((req,res) => {
        headers = req.headers;
        res.setHeader('content-length', 6); res.end('abcdef');
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      const manager = new ClientManager();
      try {
        const source = new S3DataSource({ endpoint:'http://s3.internal:9000', region:'us-east-1',
          credentials:{accessKeyId:'test-access',secretAccessKey:'test-secret'} },
          {concurrency:1,connectTimeoutMs:500,requestTimeoutMs:1000},
          {key:'fixture',transport:new PortForwardTransport(async () => ({host:'127.0.0.1',port:server.address().port}))});
        const client = await manager.get(source);
        const value = await client.readObject('bucket','key',{maxBytes:3});
        assert.equal(Buffer.from(value.bytes).toString(),'abc');
        assert.equal(value.truncated,true);
        assert.equal(headers.host,'s3.internal:9000');
        assert.match(headers.authorization,/AWS4-HMAC-SHA256/);
        const abort = new AbortController(); abort.abort();
        await assert.rejects(client.headBucket('bucket',{signal:abort.signal}));
      } finally {
        await manager.dispose(); server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
      }
    `], { cwd: root, encoding: "utf8", timeout: 10_000 });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
