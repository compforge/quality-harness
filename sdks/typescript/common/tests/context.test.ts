import { expect, test } from "bun:test";
import { ClientManager, EnvironmentContext, type ClientFactory, type DataSource } from "../src/index.js";

class Probe {
  ready = false;
  closed = false;
  async initialize() { this.ready = true; }
  async dispose() { this.closed = true; }
}

test("environment and data factories share root ownership without fixture or source metadata", async () => {
  const clients = new ClientManager();
  const context = new EnvironmentContext({ name: "test" }, clients, performance.now() + 10_000);
  const environment: ClientFactory<Probe> = { key: "environment", createClient: () => new Probe() };
  const source: DataSource<Probe> = { key: "db", createClient: () => new Probe() };
  const first = await context.clients.get(environment);
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
  expect(new EnvironmentContext({}, new ClientManager(), performance.now() - 1).remainingMs).toBe(0);
});
