import { expect, test } from "bun:test";
import { ClientManager, EnvironmentContext, type ClientProvider, type DataSource } from "../src/index.js";

class Probe {
  ready = false;
  closed = false;
  async initialize() { this.ready = true; }
  async dispose() { this.closed = true; }
}

test("environment and data providers share root ownership without fixture or source metadata", async () => {
  const clients = new ClientManager();
  const environment = { name: "test", clientKey: "environment", createClient: () => new Probe() } satisfies ClientProvider<Probe> & { name: string };
  const context = new EnvironmentContext(environment, clients, performance.now() + 10_000);
  const source: DataSource<Probe> = { clientKey: "db", createClient: () => new Probe() };
  const first = await context.clients.get(context.environment);
  const second = await context.clients.get(source);
  expect(first).toBe(await context.clients.get({ ...environment }));
  expect(first).not.toBe(second);
  expect(context.remainingMs).toBeGreaterThan(0);
  expect(first.ready && second.ready).toBe(true);
  expect(first.closed || second.closed).toBe(false);
  expect("phase" in context).toBe(false);
  // @ts-expect-error Context consumers receive borrowing, not disposal authority.
  const invalid = () => context.clients.dispose();
  void invalid;
  await clients.dispose();
  expect(first.closed && second.closed).toBe(true);
});

test("expired context budget is zero", () => {
  expect(new EnvironmentContext({ name: "local" }, new ClientManager(), performance.now() - 1).remainingMs).toBe(0);
});
