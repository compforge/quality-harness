import { expect, test } from "bun:test";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer as createTlsServer } from "node:https";
import { readFileSync } from "node:fs";
import { ClientManager } from "../src/client-manager";
import { S3Client, S3DataSource, type S3Target } from "../src/s3";
import { DirectTransport, PortForwardTransport } from "../src/transport";

const limits = { concurrency: 2, connectTimeoutMs: 500, requestTimeoutMs: 2000 };
const credentials = { accessKeyId: "test-access", secretAccessKey: "test-secret", sessionToken: "test-session" };
type Handler = (req: IncomingMessage, res: ServerResponse) => void;

async function server(handler: Handler) {
  const instance = createServer(handler);
  await new Promise<void>(resolve => instance.listen(0, "127.0.0.1", resolve));
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("missing address");
  return { port: address.port, endpoint: `http://127.0.0.1:${address.port}`,
    async close() { instance.closeAllConnections(); await new Promise<void>(resolve => instance.close(() => resolve())); } };
}

function target(endpoint: string): S3Target { return { endpoint, region: "us-east-1", credentials }; }

test("S3 DataSource shares clients, hashes credentials/policy/route, snapshots identity and rejects reuse after disposal", async () => {
  let calls = 0;
  const fixture = await server((_req, res) => { calls++; res.end(); });
  const config = target(fixture.endpoint);
  const source = new S3DataSource(config, limits);
  const manager = new ClientManager();
  try {
    expect(source.key).not.toContain(credentials.secretAccessKey);
    expect(source.key).toBe(new S3DataSource(config, limits).key);
    expect(source.key).not.toBe(new S3DataSource(config, { ...limits, concurrency: 1 }).key);
    expect(source.key).not.toBe(new S3DataSource({ ...config, credentials: { ...credentials, sessionToken: "other" } }, limits).key);
    const route = new PortForwardTransport(async endpoint => endpoint);
    expect(new S3DataSource(config, limits, { key: "cluster-a", transport: route }).key)
      .not.toBe(new S3DataSource(config, limits, { key: "cluster-b", transport: route }).key);
    config.endpoint = "http://not-the-original.example";
    const [a, b] = await Promise.all([manager.get(source), manager.get(source)]);
    expect(a).toBe(b);
    expect(calls).toBe(0);
    await a.headBucket("bucket");
    expect(calls).toBe(1);
    await Promise.all([manager.dispose(), manager.dispose()]);
    expect(() => a.headBucket("bucket")).toThrow();
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 preserves logical Host through TCP routing and signs encoded object keys and temporary credentials", async () => {
  let host: string | undefined, url = "", auth = "", token: string | undefined;
  const fixture = await server((req, res) => {
    host = req.headers.host; url = req.url!; auth = req.headers.authorization!;
    token = req.headers["x-amz-security-token"] as string;
    res.writeHead(200, { "content-length": "42", etag: '"etag"', "x-amz-meta-purpose": "fixture" }); res.end();
  });
  const client = new S3Client({ resolve: async () => target("http://s3.internal:9000"),
    transports: [new PortForwardTransport(async () => ({ host: "127.0.0.1", port: fixture.port }))] }, limits);
  try {
    await client.initialize();
    const value = await client.headObject("bucket", "a b/中文+?#%.txt");
    expect(host).toBe("s3.internal:9000");
    expect(decodeURIComponent(url)).toBe("/bucket/a b/中文+?#%.txt");
    expect(auth).toContain("AWS4-HMAC-SHA256");
    expect(auth).toContain("host");
    expect(token).toBe(credentials.sessionToken);
    expect(value.size).toBe(42);
    expect(value.metadata.purpose).toBe("fixture");
  } finally { await client.dispose(); await fixture.close(); }
});

test("S3 list APIs return a single bounded page and preserve continuation and versioning", async () => {
  const urls: URL[] = [];
  const fixture = await server((req, res) => {
    const url = new URL(req.url!, "http://localhost"); urls.push(url);
    res.setHeader("content-type", "application/xml");
    if (url.searchParams.has("versioning")) res.end('<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>');
    else if (url.searchParams.has("list-type")) res.end('<ListBucketResult><IsTruncated>true</IsTruncated><NextContinuationToken>next</NextContinuationToken><Contents><Key>a</Key><Size>12</Size></Contents><CommonPrefixes><Prefix>dir/</Prefix></CommonPrefixes></ListBucketResult>');
    else res.end('<ListAllMyBucketsResult><Buckets><Bucket><Name>bucket</Name></Bucket></Buckets><ContinuationToken>more</ContinuationToken></ListAllMyBucketsResult>');
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    const page = await client.listObjects("bucket", { maxKeys: 3, prefix: "a/", continuationToken: "previous" });
    expect(page).toEqual({ objects: [{ key: "a", size: 12, etag: undefined, lastModified: undefined }], prefixes: ["dir/"], truncated: true, continuationToken: "next" });
    expect(urls[0]!.searchParams.get("max-keys")).toBe("3");
    expect(urls[0]!.searchParams.get("continuation-token")).toBe("previous");
    expect((await client.listBuckets({ maxBuckets: 2 })).continuationToken).toBe("more");
    expect(await client.getBucketVersioning("bucket")).toBe("enabled");
    expect(() => client.listObjects("bucket", { maxKeys: 0 })).toThrow();
    expect(urls).toHaveLength(3);
  } finally { await manager.dispose(); await fixture.close(); }
});

test.each([403, 404, 503])("S3 retains HTTP %d and never retries a protocol failure", async status => {
  let calls = 0;
  const fixture = await server((_req, res) => { calls++; res.writeHead(status); res.end(); });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    await expect(client.headObject("bucket", "key")).rejects.toMatchObject({ $metadata: { httpStatusCode: status } });
    expect(calls).toBe(1);
  } finally { await manager.dispose(); await fixture.close(); }
});

test.each([0, 4, 5, 10])("S3 bounded reads retain at most 5 bytes of a %d-byte object", async size => {
  const fixture = await server((_req, res) => { res.setHeader("content-length", size); res.end("x".repeat(size)); });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    const value = await client.readObject("bucket", "key", { maxBytes: 5 });
    expect(value.bytes.length).toBe(Math.min(size, 5));
    expect(value.truncated).toBe(size > 5);
    expect(value.size).toBe(size);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 deadlines cover queued requests and response bodies; aborted queue entries never send", async () => {
  let calls = 0;
  let started!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const fixture = await server((_req, res) => { calls++; res.writeHead(200); res.write("x"); started(); });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), { ...limits, concurrency: 1, requestTimeoutMs: 250 }));
    const first = client.readObject("bucket", "key", { maxBytes: 100 }).catch(error => error);
    await received;
    const abort = new AbortController();
    const queued = client.headBucket("bucket", { signal: abort.signal });
    abort.abort(new Error("cancel queued"));
    await expect(queued).rejects.toThrow("cancel queued");
    expect(await first).toBeInstanceOf(Error);
    expect(calls).toBe(1);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 dispose aborts active body reads and queued work and is idempotent", async () => {
  let started!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const fixture = await server((_req, res) => { res.writeHead(200); res.write("x"); started(); });
  const client = new S3Client({ resolve: async () => target(fixture.endpoint), transports: [new DirectTransport()] }, { ...limits, concurrency: 1 });
  try {
    await client.initialize();
    const first = client.readObject("bucket", "key", { maxBytes: 100 }).catch(error => error);
    await received;
    const queued = client.headBucket("bucket").catch(error => error);
    await Promise.all([client.dispose(), client.dispose()]);
    expect(await first).toBeInstanceOf(Error);
    expect(await queued).toBeInstanceOf(Error);
  } finally { await client.dispose(); await fixture.close(); }
});

test("S3 TLS tunnel retains certificate validation and original server identity", async () => {
  const pem = (name: string) => readFileSync(new URL(`./fixtures/pod-log-tls/${name}.pem`, import.meta.url), "utf8");
  const instance = createTlsServer({ key: pem("server-key"), cert: pem("server-cert") }, (_req, res) => res.end());
  await new Promise<void>(resolve => instance.listen(0, "127.0.0.1", resolve));
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("missing TLS port");
  const route = new PortForwardTransport(async () => ({ host: "127.0.0.1", port: address.port }));
  const trusted = new S3Client({ resolve: async () => ({ ...target("https://localhost:9000"), ca: pem("ca") }), transports: [route] }, limits);
  const untrusted = new S3Client({ resolve: async () => target("https://localhost:9000"), transports: [route] }, limits);
  try {
    await trusted.initialize(); await untrusted.initialize();
    await trusted.headBucket("bucket");
    await expect(untrusted.headBucket("bucket")).rejects.toThrow();
  } finally {
    await trusted.dispose(); await untrusted.dispose(); instance.closeAllConnections();
    await new Promise<void>(resolve => instance.close(() => resolve()));
  }
});

test("S3 failed initialization can be retried by ClientManager", async () => {
  let attempts = 0;
  const source = { key: "s3-init-retry", createClient: (signal: AbortSignal) => new S3Client({
    resolve: async () => { if (++attempts === 1) throw new Error("resolve failed"); return target("http://localhost:1"); },
    transports: [new DirectTransport()],
  }, limits, { signal }) };
  const manager = new ClientManager();
  try {
    await expect(manager.get(source)).rejects.toThrow("resolve failed");
    expect(await manager.get(source)).toBeInstanceOf(S3Client);
    expect(attempts).toBe(2);
  } finally { await manager.dispose(); }
});

test("S3 request pool limits active requests and canceled reads release capacity", async () => {
  let active = 0, peak = 0;
  const fixture = await server((_req, res) => {
    active++; peak = Math.max(peak, active);
    setTimeout(() => { active--; res.end(); }, 10);
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    await Promise.all(Array.from({ length: 8 }, () => client.headBucket("bucket")));
    expect(peak).toBe(2);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 chunked reads truncate without Content-Length and preserve short complete streams", async () => {
  const fixture = await server((req, res) => {
    res.write("ab"); res.end(req.url!.includes("long") ? "cdef" : "c");
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    expect((await client.readObject("bucket", "long", { maxBytes: 3 })).truncated).toBe(true);
    expect((await client.readObject("bucket", "short", { maxBytes: 3 })).truncated).toBe(false);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 initialization canceled during resolution cannot acquire a route", async () => {
  let resolve!: (value: S3Target) => void, routes = 0;
  const pending = new Promise<S3Target>(done => { resolve = done; });
  const client = new S3Client({ resolve: () => pending,
    transports: [new PortForwardTransport(async value => { routes++; return value; })] }, limits);
  const initialization = client.initialize().catch(error => error);
  const disposal = client.dispose();
  resolve(target("http://localhost:1"));
  await disposal;
  expect(await initialization).toBeInstanceOf(Error);
  expect(routes).toBe(0);
});

test("S3 mapped virtual-host addressing is rejected rather than silently changing TLS identity", async () => {
  const client = new S3Client({ resolve: async () => ({ ...target("https://s3.internal"), forcePathStyle: false }),
    transports: [new PortForwardTransport(async () => ({ host: "127.0.0.1", port: 12345 }))] }, limits);
  try { await expect(client.initialize()).rejects.toThrow("path-style"); }
  finally { await client.dispose(); }
});
