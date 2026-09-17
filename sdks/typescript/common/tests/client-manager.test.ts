import { expect, test } from "bun:test";
import { ClientManager } from "../src/client-manager.js";
import type { Client } from "../src/client.js";

function gate() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

test("same identity joins initialization; queries remain independent; dependencies close last", async () => {
  const clients = new ClientManager();
  const order: string[] = [];
  let starts = 0;
  const ready = gate();
  await clients.get({ clientKey: "k8s", createClient: () => ({ initialize: async () => {}, dispose: async () => { order.push("k8s"); } }) });
  const source = { clientKey: "db", createClient: () => ({
    initialize: async () => { starts++; await ready.promise; },
    query: async (id: string) => id,
    dispose: async () => { order.push("db"); },
  }) };
  const first = clients.get(source);
  const second = clients.get({ ...source });
  ready.release();
  const [a, b] = await Promise.all([first, second]);
  expect(a).toBe(b);
  expect(starts).toBe(1);
  expect(await Promise.all([a.query("a"), b.query("b")])).toEqual(["a", "b"]);
  expect(clients.dispose()).toBe(clients.dispose());
  await clients.dispose();
  expect(order).toEqual(["db", "k8s"]);
  expect(() => clients.get(source)).toThrow("disposed");
});

test("failed initialization cleans before retry and doesn't close borrowed dependencies", async () => {
  const clients = new ClientManager();
  const order: string[] = [];
  await clients.get({ clientKey: "k8s", createClient: () => ({ initialize: async () => {}, dispose: async () => { order.push("k8s"); } }) });
  await expect(clients.get({ clientKey: "db", createClient: () => ({
    initialize: async () => { throw new Error("unavailable"); },
    dispose: async () => { order.push("partial"); },
  }) })).rejects.toThrow("unavailable");
  await clients.get({ clientKey: "db", createClient: () => ({ initialize: async () => { order.push("retry"); }, dispose: async () => { order.push("db"); } }) });
  expect(order).toEqual(["partial", "retry"]);
  await clients.dispose();
  expect(order).toEqual(["partial", "retry", "db", "k8s"]);
});

test("dispose waits for late initialization and does not leak its client", async () => {
  const clients = new ClientManager();
  const started = gate();
  const ready = gate();
  let closed = 0;
  const pending = clients.get({ clientKey: "db", createClient: () => ({
    initialize: async () => { started.release(); await ready.promise; },
    dispose: async () => { closed++; },
  }) }).catch(error => error);
  await started.promise;
  const closing = clients.dispose();
  ready.release();
  expect(await pending).toBeInstanceOf(Error);
  await closing;
  expect(closed).toBe(1);
});

test("dispose before construction avoids external work; cleanup failures don't block other clients", async () => {
  const clients = new ClientManager();
  let started = false;
  const pending = clients.get({ clientKey: "new", createClient: (): Client => {
    started = true;
    return { initialize: async () => {}, dispose: async () => {} };
  } }).catch(error => error);
  await clients.dispose();
  expect(await pending).toBeInstanceOf(Error);
  expect(started).toBe(false);
  const other = new ClientManager();
  const closed: string[] = [];
  for (const key of ["a", "b"]) await other.get({ clientKey: key, createClient: () => ({
    initialize: async () => {}, dispose: async () => { closed.push(key); throw new Error(key); },
  }) });
  await expect(other.dispose()).rejects.toThrow("Client cleanup failed");
  expect(closed).toEqual(["b", "a"]);
});


test("a concurrent retry waits for failed initialization cleanup", async () => {
  const clients = new ClientManager();
  const cleaning = gate();
  const cleaned = gate();
  let starts = 0;
  const source = { clientKey: "db", createClient: () => ({
    initialize: async () => { starts++; throw new Error("offline"); },
    dispose: async () => { cleaning.release(); await cleaned.promise; },
  }) };
  const first = clients.get(source);
  const failure = first.catch(error => error);
  await cleaning.promise;
  expect(clients.get(source)).toBe(first);
  expect(starts).toBe(1);
  cleaned.release();
  expect(await failure).toBeInstanceOf(Error);
  await expect(clients.get(source)).rejects.toThrow("offline");
  expect(starts).toBe(2);
  await clients.dispose();
});

test("failed cleanup poisons the key and remains visible at root disposal", async () => {
  const clients = new ClientManager();
  const initialization = new Error("initialization failed");
  const cleanup = new Error("cleanup failed");
  let created = 0, closed = 0;
  const source = { clientKey: "broken", createClient: () => {
    created++;
    return {
      initialize: async () => { throw initialization; },
      dispose: async () => { closed++; throw cleanup; },
    };
  } };
  let observed: unknown;
  try { await clients.get(source); } catch (error) { observed = error; }
  expect(observed).toBeInstanceOf(AggregateError);
  expect((observed as AggregateError).errors).toEqual([initialization, cleanup]);
  await expect(clients.get(source)).rejects.toBe(observed);
  const closing = clients.dispose();
  expect(clients.dispose()).toBe(closing);
  await expect(closing).rejects.toMatchObject({ errors: [cleanup] });
  expect(created).toBe(1);
  expect(closed).toBe(1);
});

test("an initialization AggregateError with successful cleanup does not poison disposal", async () => {
  const clients = new ClientManager();
  const error = new AggregateError([new Error("connection failed")], "initialization failed");
  await expect(clients.get({ clientKey: "failed", createClient: () => ({
    initialize: async () => { throw error; },
    dispose: async () => {},
  }) })).rejects.toBe(error);
  await clients.dispose();
});

test("factories borrow shared dependencies through their provider and close consumers first", async () => {
  const clients = new ClientManager();
  const order: string[] = [];
  let dependencyStarts = 0;
  const dependency = { clientKey: "environment", createClient: () => ({
    initialize: async () => { dependencyStarts++; },
    dispose: async () => { order.push("environment"); },
  }) };
  const borrowed: Client[] = [];
  const consumer = (key: string) => ({
    clientKey: key, createClient: (provider: Pick<ClientManager, "get">, signal: AbortSignal) => ({
      initialize: async () => {
        signal.throwIfAborted();
        borrowed.push(await provider.get(dependency));
      },
      dispose: async () => { order.push(key); },
    }),
  });
  await Promise.all([clients.get(consumer("db")), clients.get(consumer("log"))]);
  expect(dependencyStarts).toBe(1);
  expect(borrowed[0]).toBe(borrowed[1]);
  await clients.dispose();
  expect(order.slice(0, 2).sort()).toEqual(["db", "log"]);
  expect(order[2]).toBe("environment");
});
