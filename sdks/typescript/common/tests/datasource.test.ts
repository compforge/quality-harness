import { expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { ClientManager, clientKey, type Service, type ServiceDataSource, type Client, type DataSourceClient, type DataSource } from "../src/index.js";

test("datasource description is optional discovery metadata, not client identity", async () => {
  const source: DataSource<Client> = {
    clientKey: "shared-db",
    createClient: () => ({ initialize: async () => {}, dispose: async () => {} }),
  };
  expect(source.description).toBeUndefined();
  const described: DataSource<Client> = { ...source, description: "Read-only conversation history" };
  const clients = new ClientManager();
  try {
    expect(described.description).toBe("Read-only conversation history");
    expect(await clients.get(described)).toBe(await clients.get(source));
  } finally { await clients.dispose(); }
});

test("source identity includes credentials and ignores configuration key order", () => {
  const key = clientKey("mysql", { host: "db", password: "secret" });
  expect(key).toBe(clientKey("mysql", { password: "secret", host: "db" }));
  expect(key).not.toContain("secret");
  expect(key).not.toBe(clientKey("mysql", { host: "db", password: "other" }));
});

test("service associations share the source client without owning its lifetime", async () => {
  let starts = 0;
  let closes = 0;
  const source = { clientKey: "db", createClient: () => ({
    initialize: async () => { starts++; },
    dispose: async () => { closes++; },
  }) };
  const component = { name: "api", repository: { forge: { name: "github" }, path: "example/app" } };
  const environment = { name: "test" };
  const bindings: ServiceDataSource<Service, Client>[] = ["one", "two"].map(name => ({
    service: { name, component, environment, workloads: [] }, source,
  }));
  const clients = new ClientManager();
  try {
    const [one, two] = await Promise.all(bindings.map(binding => clients.get(binding.source)));
    expect(one).toBe(two);
    expect(starts).toBe(1);
    expect(closes).toBe(0);
  } finally { await clients.dispose(); }
  expect(closes).toBe(1);
});

test("published ESM imports and executes on Node", () => {
  const output = execFileSync(process.env.NODE ?? "node", ["--input-type=module", "-e",
    'import { ClientManager, clientKey } from "./dist/index.js"; const c = new ClientManager(); await c.dispose(); console.log(clientKey("db", {}));',
  ], { cwd: import.meta.dir + "/..", encoding: "utf8" });
  expect(output.trim()).toMatch(/^db:[a-f0-9]{64}$/);
});


test("mask is a data client projection, independent of borrowing and lifecycle", async () => {
  let initialized = 0;
  let closed = 0;
  const source: DataSource<DataSourceClient> = {
    clientKey: "masked-db",
    createClient: () => ({
      initialize: async () => { initialized++; },
      dispose: async () => { closed++; },
      mask: () => ({ host: "db", database: "records" }),
    }),
  };
  const clients = new ClientManager();
  try {
    const client = await clients.get(source);
    expect(client.mask()).toEqual({ host: "db", database: "records" });
    expect(client.mask()).not.toBe(client.mask());
    expect(await clients.get(source)).toBe(client);
    expect(initialized).toBe(1);
    expect(closed).toBe(0);
  } finally { await clients.dispose(); }
  expect(closed).toBe(1);
});
