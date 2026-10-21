import { expect, test } from "bun:test";
import { mkdtemp, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ClientManager, FileDataSource, type ClientProvider, type FileReadResult } from "@compforge/harness-common";
import { LocalFileDataSource, PodFileDataSource, FileError } from "../src/file";
import type { PodFileAccess } from "../src/file/pod";
import { runArgv, type ExecResult, type RunOptions } from "../src/kubernetes/executor";

const readOptions = { maxBytes: 1024, maxTotalBytes: 4096, timeoutMs: 1000 };
const target = { namespace: "test", pod: "worker-1", container: "app" };
const success: ExecResult = { ok: true, exitCode: 0, stdout: "", stderr: "", durationMs: 1, timedOut: false, command: [] };
function value(result: FileReadResult): Uint8Array {
  if (result.status === "error") throw result.error;
  return result.bytes;
}
function wire(index: number, bytes: Uint8Array, status = 0): Buffer {
  const payload = Buffer.concat([bytes, Buffer.from([0]), Buffer.from(String(status).padStart(3, "0"))]);
  return Buffer.from(`${index} ${payload.toString("base64")}\n`);
}
function fixture(exec: PodFileAccess["exec"], key = "cluster-a") {
  let initialized = 0, disposed = 0;
  const access: PodFileAccess = { exec, async initialize() { initialized++; }, async dispose() { disposed++; } };
  const provider: ClientProvider<PodFileAccess> = { clientKey: key, createClient: () => access };
  return { provider, get initialized() { return initialized; }, get disposed() { return disposed; } };
}

/** @case id=file-fresh-read,desc=Shared clients reopen mounted paths without sharing previous bytes. */
test("local groups preserve binary/empty data, continue after file failures and observe replacement", async () => {
  const directory = await mkdtemp(join(tmpdir(), "file-source-"));
  const manager = new ClientManager();
  try {
    const path = join(directory, "config.yaml"), link = join(directory, "mounted.yaml"), empty = join(directory, "empty");
    const missing = join(directory, "missing"), large = join(directory, "large");
    const bytes = new Uint8Array([0, 255, 128, 10]);
    await writeFile(path, bytes); await symlink(path, link); await writeFile(empty, ""); await writeFile(large, "12345");
    const source = new LocalFileDataSource([link, missing, large, empty], { concurrency: 1 });
    expect(source).toBeInstanceOf(FileDataSource); expect("kind" in source).toBe(false); expect("id" in source).toBe(false);
    const client = await manager.get(source);
    expect(client).toBe(await manager.get(new LocalFileDataSource(source.paths, { concurrency: 1 })));
    const result = await client.read({ ...readOptions, maxBytes: 4 });
    expect(result.map(item => item.path)).toEqual([...source.paths]);
    expect(value(result[0]!)).toEqual(bytes);
    expect(result[1]).toMatchObject({ status: "error", error: { kind: "resource_not_found" } });
    expect(result[2]).toMatchObject({ status: "error", error: { kind: "limit_exceeded" } });
    expect(value(result[3]!)).toEqual(new Uint8Array());
    value(result[0]!).fill(1);
    expect(value((await client.read(readOptions))[0]!)).toEqual(bytes);
    await writeFile(join(directory, "next"), "new"); await rename(join(directory, "next"), path);
    expect(new TextDecoder().decode(value((await client.read(readOptions))[0]!))).toBe("new");
    await manager.dispose(); await expect(client.read(readOptions)).rejects.toThrow("disposed");
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
});

test("local per-file zero limits preserve empty files and reject nonregular inputs independently", async () => {
  const directory = await mkdtemp(join(tmpdir(), "file-empty-"));
  const manager = new ClientManager();
  try {
    const empty = join(directory, "empty"), nonempty = join(directory, "full");
    await writeFile(empty, ""); await writeFile(nonempty, "a");
    const client = await manager.get(new LocalFileDataSource([empty, nonempty, directory], { concurrency: 1 }));
    const result = await client.read({ ...readOptions, maxBytes: 0, maxTotalBytes: 0 });
    expect(value(result[0]!)).toEqual(new Uint8Array());
    expect(result[1]).toMatchObject({ status: "error", error: { kind: "limit_exceeded" } });
    expect(result[2]).toMatchObject({ status: "error", error: { kind: "unsupported_operation" } });
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
});

/** @case id=file-group-one-exec,desc=Binary, missing, oversized and empty files share one bounded exec. */
test("Pod groups use one real exec with argv-safe paths and independent file results", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pod-file-exec-"));
  const manager = new ClientManager();
  let calls = 0;
  const access = fixture(async (_namespace, actualTarget, command, options) => {
    calls++; expect(actualTarget).toEqual(target);
    expect(command.slice(0, 2)).toEqual(["sh", "-c"]); expect(options?.collectStdout).toBe(false);
    return runArgv(command, options);
  });
  try {
    const path = join(directory, "config $(never); ' quoted\nname"), empty = join(directory, "empty"), large = join(directory, "large");
    const bytes = new Uint8Array([255, 0, 1, 128, 10]);
    await writeFile(path, bytes); await writeFile(empty, ""); await writeFile(large, "123456");
    const paths = [path, join(directory, "missing"), large, empty];
    const client = await manager.get(new PodFileDataSource(access.provider, target, paths, { concurrency: 1 }));
    const result = await client.read({ ...readOptions, maxBytes: 5 });
    expect(calls).toBe(1); expect(result.map(item => item.path)).toEqual(paths);
    expect(value(result[0]!)).toEqual(bytes);
    expect(result[1]).toMatchObject({ status: "error", error: { kind: "operation_failed", code: 1 } });
    expect(result[2]).toMatchObject({ status: "error", error: { kind: "limit_exceeded" } });
    expect(value(result[3]!)).toEqual(new Uint8Array());
    await writeFile(path, "changed");
    const next = await client.read(readOptions); expect(new TextDecoder().decode(value(next[0]!))).toBe("changed"); expect(calls).toBe(2);
    const failed = await client.read({ ...readOptions, maxBytes: 0 });
    expect(failed[0]).toMatchObject({ status: "error", error: { kind: "limit_exceeded" } });
    expect(value(failed[3]!)).toHaveLength(0);
    expect(access.initialized).toBe(1); await client.dispose(); expect(access.disposed).toBe(0);
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
  expect(access.disposed).toBe(1);
});

test("source identity snapshots the complete group, target and capacity", () => {
  const access = fixture(async () => success), paths = ["/config", "/secret"], mutable = { ...target }, options = { concurrency: 2 };
  const source = new PodFileDataSource(access.provider, mutable, paths, options);
  paths[0] = "/changed"; mutable.pod = "changed"; options.concurrency = 9;
  expect(source.paths).toEqual(["/config", "/secret"]); expect(source.target).toEqual(target);
  expect(source.clientKey).toBe(new PodFileDataSource(access.provider, target, ["/config", "/secret"], { concurrency: 2 }).clientKey);
  for (const changed of [{ ...target, pod: "other" }, { ...target, container: "sidecar" }, { ...target, namespace: "other" }]) {
    expect(new PodFileDataSource(access.provider, changed, source.paths, { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
  }
  expect(new PodFileDataSource(access.provider, target, ["/config"], { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
  expect(new PodFileDataSource(access.provider, target, source.paths, { concurrency: 1 }).clientKey).not.toBe(source.clientKey);
  expect(new PodFileDataSource(fixture(async () => success, "other-cluster").provider, target, source.paths, { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
});

test("split response frames do not retain mutable byte buffers and accept arbitrary file contents", async () => {
  const bytes = Buffer.from([0, 255, 10, 13, 128]);
  const access = fixture(async (_ns, _target, _command, options) => {
    const response = wire(0, bytes);
    for (const byte of response) { const reused = Buffer.from([byte]); options?.onStdoutBytes?.(reused); reused.fill(42); }
    return success;
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, ["/config"], { concurrency: 1 }));
    expect(value((await client.read(readOptions))[0]!)).toEqual(bytes);
  } finally { await manager.dispose(); }
});

for (const response of [Buffer.from("0 !!!\n"), wire(1, Buffer.alloc(0)), wire(0, Buffer.alloc(0)).subarray(0, -1),
  Buffer.from("0 " + "a".repeat(100)), Buffer.concat([wire(0, Buffer.alloc(0)), wire(0, Buffer.alloc(0))]), Buffer.from([255])]) {
  test(`malformed or oversized group response rejects without replay (${response.length} bytes)`, async () => {
    let calls = 0;
    const access = fixture(async (_ns, _target, _command, options) => { calls++; options?.onStdoutBytes?.(response); return success; });
    const manager = new ClientManager();
    try {
      const client = await manager.get(new PodFileDataSource(access.provider, target, ["/config"], { concurrency: 1 }));
      await expect(client.read({ ...readOptions, maxBytes: 4 })).rejects.toMatchObject({ kind: "invalid_response" });
      expect(calls).toBe(1);
    } finally { await manager.dispose(); }
  });
}

test("whole-group byte budgets abort streaming; transport failures never expose stderr", async () => {
  let aborted = false;
  const access = fixture(async (_ns, _target, _command, options) => {
    options?.onStdoutBytes?.(Buffer.concat([wire(0, Buffer.from("abc")), wire(1, Buffer.from("def"))]));
    aborted = options!.signal!.aborted; return success;
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, ["/a", "/b"], { concurrency: 1 }));
    await expect(client.read({ ...readOptions, maxTotalBytes: 5 })).rejects.toMatchObject({ kind: "limit_exceeded" }); expect(aborted).toBe(true);
    const failed = fixture(async () => ({ ...success, ok: false, exitCode: 1, stderr: "secret-value" }), "failed");
    const unreadable = await manager.get(new PodFileDataSource(failed.provider, target, ["/config"], { concurrency: 1 }));
    const error = await unreadable.read(readOptions).catch(error => error);
    expect(error).toBeInstanceOf(FileError); expect(error.kind).toBe("operation_failed"); expect(String(error)).not.toContain("secret-value");
  } finally { await manager.dispose(); }
});

test("total budget stops an unterminated file while its encoded response is still arriving", async () => {
  let aborted = false;
  const access = fixture(async (_ns, _target, _command, options) => {
    options?.onStdoutBytes?.(wire(0, Buffer.alloc(100)).subarray(0, -1));
    aborted = options!.signal!.aborted;
    return waitForAbort(options);
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, ["/config"], { concurrency: 1 }));
    await expect(client.read({ ...readOptions, maxTotalBytes: 5 })).rejects.toMatchObject({ kind: "limit_exceeded" });
    expect(aborted).toBe(true);
  } finally { await manager.dispose(); }
});

function waitForAbort(options?: RunOptions): Promise<ExecResult> {
  return new Promise(resolve => {
    const end = () => resolve({ ...success, ok: false });
    if (options?.signal?.aborted) end(); else options?.signal?.addEventListener("abort", end, { once: true });
  });
}

test("one group deadline covers queueing and cancellation never starts queued I/O", async () => {
  let started = 0, entered!: () => void;
  const ready = new Promise<void>(resolve => entered = resolve);
  const access = fixture(async (_ns, _target, _command, options) => { started++; entered(); return waitForAbort(options); });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, ["/config", "/secret"], { concurrency: 1 }));
    const abort = new AbortController(), first = client.read({ ...readOptions, signal: abort.signal }).catch(error => error);
    await ready;
    await expect(client.read({ ...readOptions, timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
    const queuedAbort = new AbortController(), queued = client.read({ ...readOptions, signal: queuedAbort.signal }).catch(error => error);
    queuedAbort.abort(new Error("caller cancelled")); expect((await queued).message).toBe("caller cancelled"); expect(started).toBe(1);
    abort.abort(new Error("active cancelled")); expect((await first).message).toBe("active cancelled");
    await expect(client.read({ ...readOptions, timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" }); expect(started).toBe(2);
    const nextReady = new Promise<void>(resolve => entered = resolve), active = client.read(readOptions).catch(error => error);
    await nextReady;
    const waiting = client.read(readOptions).catch(error => error); await manager.dispose();
    expect((await active).message).toContain("disposed"); expect((await waiting).message).toContain("disposed"); expect(started).toBe(3);
  } finally { await manager.dispose(); }
});

test("invalid groups and limits fail before file access", async () => {
  let reads = 0;
  const access = fixture(async () => { reads++; return success; });
  for (const paths of [[], [""], ["/same", "/same"], ["/nul\0"]]) {
    expect(() => new LocalFileDataSource(paths, { concurrency: 1 })).toThrow("paths");
  }
  expect(() => new LocalFileDataSource(["/config"], { concurrency: 0 })).toThrow("concurrency");
  expect(() => new PodFileDataSource(access.provider, target, ["relative"], { concurrency: 1 })).toThrow("absolute");
  expect(() => new PodFileDataSource(access.provider, { ...target, container: "" }, ["/config"], { concurrency: 1 })).toThrow("Container");
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, ["/config"], { concurrency: 1 }));
    for (const limits of [{ maxBytes: -1 }, { maxTotalBytes: -1 }, { timeoutMs: 0 },
      { maxBytes: Number.MAX_SAFE_INTEGER }, { timeoutMs: 2 ** 31 }]) {
      await expect(client.read({ ...readOptions, ...limits })).rejects.toMatchObject({ kind: "invalid_argument" });
    }
    const abort = new AbortController(); abort.abort(new Error("pre-cancelled"));
    await expect(client.read({ ...readOptions, signal: abort.signal })).rejects.toThrow("pre-cancelled"); expect(reads).toBe(0);
  } finally { await manager.dispose(); }
});
