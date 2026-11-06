import { expect, test } from "bun:test";
import type { Endpoint } from "@compforge/harness-common";
import { endpointFromUrl, validateEndpoint } from "../src/network/endpoint";

for (const [url, expected] of [
  ["http://store.demo/path", { scheme: "http", host: "store.demo", port: 80, secure: false }],
  ["HTTPS://Store.Example:443/path", { scheme: "https", host: "store.example", port: 443, secure: true }],
  ["https://127.0.0.1:9443", { scheme: "https", host: "127.0.0.1", port: 9443, secure: true }],
  ["https://[::1]:9443/object", { scheme: "https", host: "::1", port: 9443, secure: true }],
  ["wss://stream.example", { scheme: "wss", host: "stream.example", port: 443, secure: true }],
  ["ws://stream.example", { scheme: "ws", host: "stream.example", port: 80, secure: false }],
  ["ftp://files.example:21", { scheme: "ftp", host: "files.example", port: 21, secure: false }],
  ["tls://store.demo:9443", { scheme: "tls", host: "store.demo", port: 9443, secure: true }],
  ["mysql://db.demo:3306/database", { scheme: "mysql", host: "db.demo", port: 3306 }],
] as const) {
  test(`network projection of ${url}`, () => expect(endpointFromUrl(url)).toEqual(expected));
}

test("URL projection drops credentials and resource identity without mutating the URL", () => {
  const url = new URL("https://user:password@bucket.example/object?signature=secret#fragment");
  const before = url.href;
  expect(endpointFromUrl(url)).toEqual({ scheme: "https", host: "bucket.example", port: 443, secure: true });
  expect(url.href).toBe(before);
});

for (const url of ["/relative?token=secret", "file:///private/file", "mysql://db.demo/database", "custom://db.demo", "constructor://host", "https://user:secret@host:99999/path"]) {
  test(`invalid or unresolved network address is rejected: ${url.split(":")[0]}`, () => {
    expect(() => endpointFromUrl(url)).toThrow();
    try { endpointFromUrl(url); } catch (error) {
      expect(String(error)).not.toContain("secret");
      expect((error as Error).cause).toBeUndefined();
    }
  });
}

test("validates addresses without resolving hosts or interpreting Service ownership", () => {
  for (const endpoint of [
    { host: "service.other-namespace", port: 8080 },
    { host: "2001:db8::1", port: 443, scheme: "https", secure: true },
    { host: "db.demo", port: 3306, scheme: "mysql", secure: true },
  ]) expect(() => validateEndpoint(endpoint)).not.toThrow();
});

for (const patch of [
  { host: "" }, { host: "host/path" }, { host: "user:password@host" }, { host: "[::1]" },
  { host: "host:8080" }, { host: "bad host" }, { host: "host?token=secret" },
  { port: 0 }, { port: 65536 }, { port: 3.5 }, { port: NaN },
  { scheme: "HTTPS" }, { scheme: "https:" }, { scheme: "" },
  { secure: "yes" }, { scheme: "https" }, { scheme: "https", secure: false },
  { scheme: "http", secure: true }, { scheme: "tls" }, { scheme: "tls", secure: false },
]) {
  test(`invalid endpoint fields are rejected: ${JSON.stringify(patch)}`, () => {
    expect(() => validateEndpoint({ host: "host", port: 443, ...patch } as Endpoint)).toThrow();
  });
}
