import { expect, test } from "bun:test";
import { DataLoader } from "../src/data-loader";

const options = () => ({ maxEntries: 8, maxBytes: 4096, signal: new AbortController().signal });

test("single-round reads share work, values and synchronous/asynchronous failures; next scope refreshes", async () => {
  let calls = 0;
  const load = async () => { calls++; return { value: 1 }; };
  const scope = new DataLoader(options());
  const [a, b] = await Promise.all([scope.read("key", load), scope.read("key", load)]);
  expect(a).toBe(b); expect(calls).toBe(1);
  for (const asynchronous of [false, true]) {
    let failures = 0;
    const error = new Error("read failed");
    const fail = () => { failures++; if (asynchronous) return Promise.reject(error); throw error; };
    for (let i = 0; i < 2; i++) expect(await scope.read(asynchronous, fail).catch(e => e)).toBe(error);
    expect(failures).toBe(1);
  }
  await scope.close();
  const next = new DataLoader(options());
  await next.read("key", load); expect(calls).toBe(2); await next.close();
});

test("capacity bypass and oversized/nonserializable results do not become cached", async () => {
  const scope = new DataLoader({ ...options(), maxEntries: 1 });
  let finish!: (value: string) => void;
  const a = scope.read("A", () => new Promise<string>(resolve => { finish = resolve; }));
  const again = scope.read("A", async () => "wrong");
  let calls = 0;
  for (let i = 0; i < 2; i++) await scope.read("B", async () => ++calls);
  finish("ok"); expect(await a).toBe("ok"); expect(await again).toBe("ok");
  expect(calls).toBe(2); await scope.close();
  for (const value of ["too large", () => {}]) {
    const small = new DataLoader({ ...options(), maxBytes: 1 });
    let count = 0;
    for (let i = 0; i < 2; i++) await small.read("key", async () => { count++; return value; });
    expect(count).toBe(2); await small.close();
  }
});

test("waiter cancellation leaves shared operation alive", async () => {
  const scope = new DataLoader(options());
  const waiter = new AbortController();
  let finish!: (value: number) => void;
  const first = scope.read("key", () => new Promise<number>(resolve => { finish = resolve; }), waiter.signal).catch(e => e);
  const second = scope.read("key", async () => 0);
  await Promise.resolve();
  waiter.abort(new Error("wait ended"));
  expect((await first).message).toBe("wait ended");
  finish(7); expect(await second).toBe(7); await scope.close();
});

test("close aborts and joins tracked work, including uncached reads; later calls fail", async () => {
  const controller = new AbortController();
  const scope = new DataLoader({ ...options(), signal: controller.signal });
  let finish!: () => void;
  let signal!: AbortSignal;
  const pending = scope.read(undefined, s => { signal = s; return new Promise<void>(resolve => { finish = resolve; }); }).catch(e => e);
  await Promise.resolve();
  controller.abort(new Error("ended"));
  let joined = false;
  const closing = scope.close().then(() => { joined = true; });
  await Promise.resolve();
  expect(signal.aborted).toBe(true); expect(joined).toBe(false);
  finish(); await closing;
  expect((await pending).message).toBe("ended");
  await expect(scope.read("key", async () => 0)).rejects.toThrow("ended");
  await scope.close();
});
