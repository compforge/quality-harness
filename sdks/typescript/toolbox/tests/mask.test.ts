import { expect, test } from "bun:test";
import { MysqlClient } from "../src/mysql";
import { RedisClient } from "../src/redis";
import { OpenSearchClient } from "../src/opensearch";
import { S3Client } from "../src/s3";
import { DirectTransport, PodPythonTransport } from "../src/transport";

test("MySQL mask omits credentials and extra config without resolving or querying again", async () => {
  let resolutions = 0;
  let queries = 0;
  const target = Object.freeze({ host: "db", port: 3306, database: "records", user: "reader",
    password: "mysql-secret", rawConfig: "private-config" });
  const client = new MysqlClient({ resolve: async () => { resolutions++; return target; },
    transports: [new PodPythonTransport(async () => { queries++; return '{"rows":[]}'; })],
  }, { connectTimeoutMs: 100, queryTimeoutMs: 100 });
  try {
    expect(() => client.mask()).toThrow("not initialized");
    await client.initialize();
    expect(client.mask()).toEqual({ kind: "db", backend: "mysql", host: "db", port: 3306, database: "records", username: "reader" });
    expect(client.mask()).not.toBe(client.mask());
    expect(JSON.stringify(client.mask())).not.toContain("mysql-secret");
    expect(client.target).toBe(target);
    expect(resolutions).toBe(1);
    expect(queries).toBe(0);
  } finally { await client.dispose(); }
});

test("Redis mask returns detached endpoints and excludes authentication fields", async () => {
  let resolutions = 0;
  const target = { endpoints: [{ host: "redis", port: 6379 }], database: 2, useSsl: true,
    timeoutMs: 100, username: "reader", password: "redis-secret", extra: "hidden" };
  const client = new RedisClient({ resolve: async () => { resolutions++; return target; }, transports: [new DirectTransport()] });
  try {
    expect(() => client.mask()).toThrow("not initialized");
    await client.initialize();
    const masked = client.mask();
    expect(masked).toEqual({ kind: "redis", endpoints: [{ host: "redis", port: 6379 }], database: 2, useSsl: true, username: "reader" });
    (masked.endpoints as Array<{ host: string }>)[0]!.host = "changed";
    expect(client.mask().endpoints).toEqual([{ host: "redis", port: 6379 }]);
    expect(target.password).toBe("redis-secret");
    expect(resolutions).toBe(1);
  } finally { await client.dispose(); }
});

test("OpenSearch mask strips URL credentials, query and fragment and keeps the logical target", async () => {
  let resolutions = 0;
  let routes = 0;
  const node = "http://url-user:url-secret@search:9200/base?token=query-secret#fragment-secret";
  const target = { node, auth: { username: "reader", password: "auth-secret" } };
  const client = new OpenSearchClient({ resolve: async () => { resolutions++; return target; },
    transports: [{ kind: "tcp", name: "tunnel", connect: async () => { routes++; return { host: "127.0.0.1", port: 19000 }; } }],
  });
  try {
    expect(() => client.mask()).toThrow("not initialized");
    await client.initialize();
    expect(client.mask()).toEqual({ kind: "vdb", backend: "opensearch", endpoint: "http://search:9200/base", username: "reader" });
    expect(JSON.stringify(client.mask())).not.toContain("secret");
    expect(target.node).toBe(node);
    expect(resolutions).toBe(1);
    expect(routes).toBe(1);
  } finally { await client.dispose(); }
});

test("S3 mask excludes access keys, session tokens and TLS material without I/O", async () => {
  let resolutions = 0;
  const target = { endpoint: "https://s3.example", region: "test", credentials: {
    accessKeyId: "access-secret", secretAccessKey: "key-secret", sessionToken: "session-secret",
  }, ca: "certificate-secret" };
  const client = new S3Client({ resolve: async () => { resolutions++; return target; }, transports: [new DirectTransport()] },
    { concurrency: 1, connectTimeoutMs: 100, requestTimeoutMs: 100 });
  try {
    expect(() => client.mask()).toThrow("not initialized");
    await client.initialize();
    expect(client.mask()).toEqual({ kind: "s3", endpoint: "https://s3.example", region: "test", forcePathStyle: true });
    expect(JSON.stringify(client.mask())).not.toContain("secret");
    expect(target.credentials.secretAccessKey).toBe("key-secret");
    expect(resolutions).toBe(1);
  } finally { await client.dispose(); }
});
