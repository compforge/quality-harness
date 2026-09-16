import { expect, test } from "bun:test";
import { execFileSync } from "node:child_process";
import { ClientManager, dataSourceKey, type ServiceDataSource, type Client } from "../src/index.js";

test("source identity includes credentials and ignores configuration key order", () => {
  const key = dataSourceKey("mysql", { host: "db", password: "secret" });
  expect(key).toBe(dataSourceKey("mysql", { password: "secret", host: "db" }));
  expect(key).not.toContain("secret");
  expect(key).not.toBe(dataSourceKey("mysql", { host: "db", password: "other" }));
});

test("service associations share the source client without owning its lifetime", async () => {
  let starts = 0;
  let closes = 0;
  const source = { key: "db", createClient: () => ({
    initialize: async () => { starts++; },
    dispose: async () => { closes++; },
  }) };
  const bindings: ServiceDataSource<{ name: string }, Client>[] = [
    { service: { name: "one" }, source }, { service: { name: "two" }, source },
  ];
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
    'import { ClientManager, dataSourceKey } from "./dist/index.js"; const c = new ClientManager(); await c.dispose(); console.log(dataSourceKey("db", {}));',
  ], { cwd: import.meta.dir + "/..", encoding: "utf8" });
  expect(output.trim()).toMatch(/^db:[a-f0-9]{64}$/);
});
