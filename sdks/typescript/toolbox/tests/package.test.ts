import { expect, test } from "bun:test";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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
      import { ClientManager, EnvironmentContext, clientKey } from '@compforge/harness-toolbox';
      import { ToolboxError } from '@compforge/harness-toolbox';
      import { KubernetesError } from '@compforge/harness-toolbox/errors';
      import { ClientManager as Subpath } from '@compforge/harness-toolbox/client-manager';
      import { ClientManager as Common } from '@compforge/harness-common';
      import { collectClusterCompute } from '@compforge/harness-toolbox/kubernetes/compute';
      import { DirectTransport, PodRelayTransport } from '@compforge/harness-toolbox/transport';
      const relay = new PodRelayTransport({namespace:'test', startupTimeoutMs:1000, connectTimeoutMs:1000,
        maxConnections:2, maxTargets:2, maxCandidatePods:2});
      await relay.initialize(); await relay.dispose();
      assert.equal(ClientManager, Subpath);
      assert.equal(ClientManager, Common);
      assert.ok(new KubernetesError('safe', {kind:'permission_denied'}) instanceof ToolboxError);
      const forbidden = { get: async () => { throw new KubernetesError('denied',{kind:'permission_denied',code:403}); },
        list: async () => { throw new KubernetesError('denied',{kind:'permission_denied',code:403}); } };
      const compute = await collectClusterCompute(forbidden, {environment:'test', timeoutMs:1000,maxNodes:1,maxPods:1,pageSize:1});
      assert.equal(compute.nodes.status,'unavailable');
      assert.equal(compute.nodes.issues[0].kind,'permission_denied');
      assert.equal(compute.summary.capacity,undefined);
      let created = 0, closed = 0;
      const source = { clientKey: 'test', createClient: () => {
        created++; return { initialize: async () => {}, dispose: async () => { closed++; } };
      } };
      const manager = new ClientManager();
      const ctx = new EnvironmentContext({name:'fixture'}, manager, performance.now() + 1000);
      assert.ok(ctx.remainingMs > 0);
      assert.equal(ctx.clients, manager);
      assert.equal(clientKey('config', {a:1,b:2}), clientKey('config', {b:2,a:1}));
      const [a,b] = await Promise.all([manager.get(source), manager.get(source)]);
      assert.equal(a,b); assert.equal(created,1);
      const other = await manager.get({...source, clientKey:'other'});
      assert.notEqual(a,other); assert.equal(created,2);
      await manager.dispose(); await manager.dispose(); assert.equal(closed,2);
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

test("published MySQL query helpers preserve typed parameters and isolated cached rows in Node", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-query-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    installCommon(root);
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    const require = createRequire(import.meta.url);
    symlinkSync(dirname(require.resolve("mysql2/package.json")), join(root, "node_modules/mysql2"), "dir");
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { withReadScope, queryLimited } from '@compforge/harness-toolbox/mysql';
      const target = {host:'db',port:3306,database:'app',user:'reader',password:'fixture'};
      let reads = 0;
      const inner = {
        query: async (_target,sql,values) => {
          reads++; assert.ok(sql.endsWith('LIMIT 2')); assert.equal(values[0],1n);
          return [{bytes:Buffer.from('ok'),date:new Date(0)}, {extra:true}];
        },
        queryOne: async () => undefined,
        close: async () => { throw new Error('borrower must not close underlying connection'); },
      };
      const controller = new AbortController();
      const { DataLoader } = await import('@compforge/harness-toolbox/data-loader');
      const scope = new DataLoader({maxEntries:8,maxBytes:4096,signal:controller.signal});
      const cache = withReadScope(inner, scope);
      const statement = {sql:'SELECT value FROM items WHERE id = ?',values:[1n]};
      const first = await queryLimited(cache,target,statement,1);
      assert.equal(first.truncated,true);
      // Shared observations must not be mutated.
      const second = await queryLimited(cache,target,statement,1);
      assert.ok(Buffer.isBuffer(second.rows[0].bytes));
      assert.equal(second.rows[0].bytes.toString(),'ok');
      assert.ok(second.rows[0].date instanceof Date);
      assert.equal(reads,1);
      controller.abort(new Error('finished'));
      await assert.rejects(queryLimited(cache,target,statement,1),/finished/);
      await cache.close();
      await scope.close();
    `], { cwd: root, encoding: "utf8", timeout: 10_000 });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("published OpenSearch entry falls back and does not replay reads on a reused socket in Node", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-opensearch-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    installCommon(root);
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    const require = createRequire(import.meta.url);
    const link = join(root, "node_modules/@opensearch-project/opensearch");
    mkdirSync(dirname(link), { recursive: true });
    symlinkSync(dirname(require.resolve("@opensearch-project/opensearch/package.json")), link, "dir");
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { createServer } from 'node:http';
      import { openOpenSearch } from '@compforge/harness-toolbox/opensearch';
      import { DirectTransport } from '@compforge/harness-toolbox/transport';
      let queries = 0, connects = 0;
      const routes = [];
      const server = createServer((req,res) => {
        if (req.method === 'HEAD') res.end();
        else { queries++; req.socket.destroy(); }
      });
      await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
      let client;
      try {
        client = await openOpenSearch({
          resolve: async () => ({ node: 'http://missing.invalid:9200', auth: {}, requestTimeoutMs: 500 }),
          transports: [new DirectTransport(), {kind:'tcp',name:'pod-relay',connect:async () => {
            connects++; return {host:'127.0.0.1',port:server.address().port};
          }}],
        }, {onRoute: route => routes.push(route)});
        await assert.rejects(client.search('spans',{}));
        assert.equal(queries,1); assert.equal(connects,1);
        assert.deepEqual(routes,[{transport:'pod-relay',reason:'ConnectionError'}]);
      } finally {
        await client?.close(); server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
      }
    `], { cwd: root, encoding: "utf8", timeout: 5000 });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});


test("published file sources preserve Common type identity and read bytes in Node without protocol drivers", () => {
  const root = mkdtempSync(join(tmpdir(), "harness-toolbox-file-package-"));
  const target = join(root, "node_modules/@compforge/harness-toolbox");
  mkdirSync(target, { recursive: true });
  try {
    installCommon(root);
    cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
    cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
    writeFileSync(join(root, "config"), new Uint8Array([0, 255, 128, 10]));
    const result = spawnSync("node", ["--input-type=module", "-e", `
      import assert from 'node:assert/strict';
      import { ClientManager, FileDataSource as Common } from '@compforge/harness-common';
      import { FileDataSource, LocalFileDataSource, PodFileDataSource } from '@compforge/harness-toolbox/file';
      assert.equal(FileDataSource, Common);
      const source = new LocalFileDataSource(['./config', './missing'], {concurrency:1});
      assert.ok(source instanceof Common);
      const manager = new ClientManager();
      try {
        const client = await manager.get(source);
        const result = await client.read({maxBytes:4,maxTotalBytes:4,timeoutMs:1000});
        assert.equal(result[0].status, 'ok'); assert.deepEqual(result[0].bytes, new Uint8Array([0,255,128,10]));
        assert.equal(result[1].status, 'error'); assert.equal(result[1].error.kind, 'resource_not_found');
        assert.equal((await client.read({maxBytes:3,maxTotalBytes:4,timeoutMs:1000}))[0].error.kind, 'limit_exceeded');
        let execs = 0;
        const access = {clientKey:'test',createClient:() => ({
          initialize:async()=>{},dispose:async()=>{},exec:async(_namespace,_target,_command,options)=>{
            execs++;
            const wire = Buffer.concat([Buffer.from([255,0,0]), Buffer.from('000')]).toString('base64');
            const failed = Buffer.concat([Buffer.from([0]), Buffer.from('001')]).toString('base64');
            options.onStdoutBytes(Buffer.from('0 '+wire+'\\n1 '+failed+'\\n'));
            return {ok:true};
          }
        })};
        const pod = new PodFileDataSource(access, {namespace:'ns',pod:'p',container:'app'}, ['/config', '/missing'], {concurrency:1});
        assert.ok(pod instanceof Common);
        const podResults = await (await manager.get(pod)).read({maxBytes:2,maxTotalBytes:2,timeoutMs:1000});
        assert.equal(execs, 1);
        assert.equal(podResults[0].status, 'ok'); assert.deepEqual(podResults[0].bytes, new Uint8Array([255,0]));
        assert.equal(podResults[1].status, 'error'); assert.equal(podResults[1].error.kind, 'operation_failed');
      } finally { await manager.dispose(); }
    `], { cwd: root, encoding: "utf8", timeout: 10_000 });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

for (const mode of ["fallback", "no-replay", "cancel", "sentinel", "cluster"]) {
  test(`published Redis client ${mode} uses actual sockets and releases them in Node`, () => {
    const root = mkdtempSync(join(tmpdir(), "harness-toolbox-redis-package-"));
    const target = join(root, "node_modules/@compforge/harness-toolbox");
    try {
      installCommon(root);
      mkdirSync(target, { recursive: true });
      cpSync(new URL("../dist", import.meta.url), join(target, "dist"), { recursive: true });
      cpSync(new URL("../package.json", import.meta.url), join(target, "package.json"));
      const require = createRequire(import.meta.url);
      const redis = dirname(dirname(require.resolve("@redis/client")));
      mkdirSync(join(root, "node_modules/@redis"), { recursive: true });
      symlinkSync(redis, join(root, "node_modules/@redis/client"));
      const result = spawnSync("node", [fileURLToPath(new URL("./fixtures/redis-client.mjs", import.meta.url)), mode], {
        cwd: root, encoding: "utf8", timeout: 4000,
        env: { ...process.env, REDIS_PACKAGE_ENTRY: join(target, "dist/redis/index.js") },
      });
      expect(result.error).toBeUndefined();
      expect(result.status, result.stderr).toBe(0);
      expect(result.stdout.trim()).toBe("disposed");
    } finally { rmSync(root, { recursive: true, force: true }); }
  }, 5000);
}
