import { Transport as ManagedTransport } from "../src/transport";
import { expect, test } from "bun:test";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { createServer as createTlsServer } from "node:https";
import { readFileSync } from "node:fs";
import { ClientManager } from "../src/client-manager";
import { S3Client, S3DataSource, S3Error, type S3Target } from "../src/s3";
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
    expect(source.clientKey).not.toContain(credentials.secretAccessKey);
    expect(source.clientKey).toBe(new S3DataSource(config, limits).clientKey);
    expect(source.clientKey).not.toBe(new S3DataSource(config, { ...limits, concurrency: 1 }).clientKey);
    expect(source.clientKey).not.toBe(new S3DataSource({ ...config, credentials: { ...credentials, sessionToken: "other" } }, limits).clientKey);
    const route = new PortForwardTransport(async endpoint => endpoint);
    expect(new S3DataSource(config, limits, { key: "cluster-a", transport: route }).clientKey)
      .not.toBe(new S3DataSource(config, limits, { key: "cluster-b", transport: route }).clientKey);
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
  const client = new S3Client(async () => target("http://s3.internal:9000"), new ManagedTransport([new PortForwardTransport(async () => ({ host: "127.0.0.1", port: fixture.port }))]), limits);
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
    const cancellation = new Error("cancel queued");
    abort.abort(cancellation);
    await expect(queued).rejects.toBe(cancellation);
    expect(await first).toMatchObject({ name: "S3Error", kind: "timeout", code: "ETIMEDOUT" });
    expect(calls).toBe(1);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 dispose aborts active body reads and queued work and is idempotent", async () => {
  let started!: () => void;
  const received = new Promise<void>(resolve => { started = resolve; });
  const fixture = await server((_req, res) => { res.writeHead(200); res.write("x"); started(); });
  const client = new S3Client(async () => target(fixture.endpoint), new ManagedTransport([new DirectTransport()]), { ...limits, concurrency: 1 });
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
  const trusted = new S3Client(async () => ({ ...target("https://localhost:9000"), ca: pem("ca") }), new ManagedTransport([route]), limits);
  const untrusted = new S3Client(async () => target("https://localhost:9000"), new ManagedTransport([route]), limits);
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
  const source = { clientKey: "s3-init-retry", createClient: (_clients: Pick<ClientManager, "get">, signal: AbortSignal) => new S3Client(async () => { if (++attempts === 1) throw new Error("resolve failed"); return target("http://localhost:1"); }, new ManagedTransport([new DirectTransport()]), limits, { signal }) };
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
  const client = new S3Client(() => pending, new ManagedTransport([new PortForwardTransport(async value => { routes++; return value; })]), limits);
  const initialization = client.initialize().catch(error => error);
  const disposal = client.dispose();
  resolve(target("http://localhost:1"));
  await disposal;
  expect(await initialization).toBeInstanceOf(Error);
  expect(routes).toBe(0);
});

test("S3 mapped virtual-host addressing is rejected rather than silently changing TLS identity", async () => {
  const client = new S3Client(async () => ({ ...target("https://s3.internal"), forcePathStyle: false }), new ManagedTransport([new PortForwardTransport(async () => ({ host: "127.0.0.1", port: 12345 }))]), limits);
  try { await expect(client.initialize()).rejects.toThrow("path-style"); }
  finally { await client.dispose(); }
});

test("bounded S3 PUT/GET/DELETE preserves bytes, signing and the uploaded version without listing", async () => {
  const calls: string[] = [];
  let stored = Buffer.alloc(0);
  const fixture = await server((req, res) => {
    calls.push(req.method!);
    expect(req.headers.authorization).toContain("AWS4-HMAC-SHA256");
    const url = new URL(req.url!, "http://localhost");
    expect(decodeURIComponent(url.pathname)).toBe("/bucket/probe/hello.txt");
    if (req.method === "PUT") {
      const chunks: Buffer[] = [];
      req.on("data", chunk => chunks.push(chunk));
      req.on("end", () => { stored = Buffer.concat(chunks); res.writeHead(200, { "x-amz-version-id": "v/1+", "x-amz-request-id": "upload-1", "x-amz-id-2": "host-1", etag: '"etag"' }); res.end(); });
    } else if (req.method === "GET") {
      expect(url.searchParams.get("versionId")).toBe("v/1+");
      res.writeHead(200, { "content-length": String(stored.length) }); res.end(stored);
    } else {
      expect(req.method).toBe("DELETE");
      expect(url.searchParams.get("versionId")).toBe("v/1+");
      res.writeHead(204); res.end();
    }
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    const bytes = new TextEncoder().encode("hello world");
    expect(() => client.putObject("bucket", "probe/hello.txt", bytes, { maxBytes: 10 })).toThrow("maxBytes");
    const uploaded = await client.putObject("bucket", "probe/hello.txt", bytes, { maxBytes: 11, contentType: "text/plain" });
    expect(uploaded).toEqual({ etag: '"etag"', versionId: "v/1+",
      response: { httpStatusCode: 200, requestId: "upload-1", extendedRequestId: "host-1", attempts: 1 } });
    expect(stored.toString()).toBe("hello world");
    expect(calls).toHaveLength(1);
    const read = await client.readObject("bucket", "probe/hello.txt", { maxBytes: 12, versionId: uploaded.versionId });
    expect(new TextDecoder().decode(read.bytes)).toBe("hello world");
    await client.deleteObject("bucket", "probe/hello.txt", { versionId: uploaded.versionId });
    expect(calls).toEqual(["PUT", "GET", "DELETE"]);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("S3 mutations preserve AccessDenied and never retry failed writes", async () => {
  const calls: string[] = [];
  const fixture = await server((req, res) => { calls.push(req.method!); res.writeHead(403, { "content-type": "application/xml" }); res.end("<Error><Code>AccessDenied</Code><Message>denied</Message><RequestId>r1</RequestId></Error>"); });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new S3DataSource(target(fixture.endpoint), limits));
    await expect(client.putObject("bucket", "own-key", new Uint8Array([1]), { maxBytes: 1 })).rejects.toMatchObject({ name: "S3Error", code: "AccessDenied", kind: "permission_denied", $metadata: { httpStatusCode: 403 } });
    await expect(client.deleteObject("bucket", "own-key")).rejects.toMatchObject({ name: "S3Error", code: "AccessDenied" });
    expect(calls).toEqual(["PUT", "DELETE"]);
  } finally { await manager.dispose(); await fixture.close(); }
});

test("presigned GET downloads without Authorization and signs the encoded key, version and temporary token", async () => {
  const { createHash, createHmac } = await import("node:crypto");
  const { HttpClient } = await import("../src/http");
  let calls = 0;
  const fixture = await server((req, res) => {
    calls++;
    const url = new URL(req.url!, fixture.endpoint);
    const query = url.searchParams;
    expect(req.headers.authorization).toBeUndefined();
    expect(decodeURIComponent(url.pathname)).toBe("/bucket/a b/中文+?#%.txt");
    expect(query.get("versionId")).toBe("v+/=1");
    expect(query.get("X-Amz-Expires")).toBe("60");
    expect(query.get("X-Amz-Security-Token")).toBe(credentials.sessionToken);
    expect(query.get("X-Amz-SignedHeaders")).toBe("host");
    // Verify the signature independently using the actual HTTP request's canonical form.
    const encode = (value: string) => encodeURIComponent(value).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
    const canonicalQuery = [...query].filter(([key]) => key !== "X-Amz-Signature")
      .map(([key, value]) => [encode(key), encode(value)]).sort(([a], [b]) => a! < b! ? -1 : a! > b! ? 1 : 0)
      .map(([key, value]) => `${key}=${value}`).join("&");
    const canonical = ["GET", url.pathname, canonicalQuery, `host:${req.headers.host}\n`, "host", "UNSIGNED-PAYLOAD"].join("\n");
    const [accessKey, date, region, service, terminal] = query.get("X-Amz-Credential")!.split("/");
    expect(accessKey).toBe(credentials.accessKeyId);
    expect([region, service, terminal]).toEqual(["us-east-1", "s3", "aws4_request"]);
    let signingKey: Buffer = Buffer.from(`AWS4${credentials.secretAccessKey}`);
    for (const part of [date!, region!, service!, terminal!]) signingKey = createHmac("sha256", signingKey).update(part).digest();
    const message = ["AWS4-HMAC-SHA256", query.get("X-Amz-Date"), `${date}/${region}/${service}/${terminal}`,
      createHash("sha256").update(canonical).digest("hex")].join("\n");
    expect(query.get("X-Amz-Signature")).toBe(createHmac("sha256", signingKey).update(message).digest("hex"));
    res.end("hello world");
  });
  const client = new S3Client(target(fixture.endpoint), new ManagedTransport([new DirectTransport()]), limits);
  const http = new HttpClient({ endpoint: fixture.endpoint }, new ManagedTransport([new DirectTransport()]), { ...limits, maxResponseBytes: 12 });
  try {
    await client.initialize();
    const url = await client.presignGetObject("bucket", "a b/中文+?#%.txt", { expiresIn: 60, versionId: "v+/=1" });
    expect(calls).toBe(0);
    const response = await http.request(url);
    expect(response.status).toBe(200);
    expect(Buffer.from(response.body).toString()).toBe("hello world");
    expect(calls).toBe(1);
    for (const expiresIn of [0, -1, 604801, 1.5, Infinity]) {
      expect(() => client.presignGetObject("bucket", "key", { expiresIn })).toThrow("expiresIn");
    }
    const abort = AbortSignal.abort(new Error("cancelled presign"));
    await expect(client.presignGetObject("bucket", "key", { expiresIn: 60, signal: abort })).rejects.toThrow();
    expect(calls).toBe(1);
  } finally { await client.dispose(); await http.dispose(); await fixture.close(); }
});

test("presigning retains the logical host through a mapped route", async () => {
  const client = new S3Client(target("https://s3.internal:9443"), new ManagedTransport([
    new PortForwardTransport(async () => ({ host: "127.0.0.1", port: 12345 })),
  ]), limits);
  try {
    await client.initialize();
    const url = new URL(await client.presignGetObject("bucket", "hello.txt", { expiresIn: 60 }));
    expect(url.origin).toBe("https://s3.internal:9443");
    expect(url.pathname).toBe("/bucket/hello.txt");
  } finally { await client.dispose(); }
});

test("S3Error normalizes protocol facts without serializing its native cause", async () => {
  const { ToolboxError } = await import("../src/errors");
  const native = Object.assign(new Error("https://store/key?X-Amz-Signature=SECRET"), {
    name: "SignatureDoesNotMatch", $metadata: { httpStatusCode: 403, requestId: "r1", extendedRequestId: "host-1", attempts: 1 },
  });
  const error = S3Error.from(native);
  expect(error).toBeInstanceOf(ToolboxError);
  expect(error.cause).toBe(native);
  expect(S3Error.from(error)).toBe(error);
  expect(error.toJSON()).toMatchObject({ code: "SignatureDoesNotMatch", kind: "authentication_failed", httpStatus: 403,
    requestId: "r1", extendedRequestId: "host-1" });
  expect(error.$metadata.attempts).toBe(1);
  expect(JSON.stringify(error)).not.toContain("SECRET");
  expect(error.message).not.toContain("SECRET");
  expect(S3Error.from({ name: "https://store/?secret=SECRET", $metadata: { httpStatusCode: 999, requestId: "bad\nSECRET" } }).toJSON())
    .toMatchObject({ code: "S3Error", httpStatus: undefined, requestId: undefined });
});

for (const [code, status, kind] of [
  ["AccessDenied", 403, "permission_denied"], ["NotFound", 404, "resource_not_found"],
  ["NoSuchBucket", 404, "resource_not_found"], ["ExpiredToken", 400, "authentication_failed"],
  ["SlowDown", 503, "limit_exceeded"], ["RequestTimeout", 400, "timeout"],
  ["ENOTFOUND", undefined, "connection_failed"], ["ECONNRESET", undefined, "connection_lost"],
  ["ERR_TLS_CERT_ALTNAME_INVALID", undefined, "tls_verification_failed"],
] as const) test(`S3Error classifies ${code}`, () => {
  expect(S3Error.from({ code, $metadata: { httpStatusCode: status } }).kind).toBe(kind);
});
