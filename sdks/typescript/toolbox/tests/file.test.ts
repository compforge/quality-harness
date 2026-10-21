import { expect, test } from "bun:test";
import { mkdtemp, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ClientManager, FileDataSource, type ClientProvider, type FileClient, type DataSource } from "@compforge/harness-common";
import { LocalFileDataSource, PodFileDataSource, FileError } from "../src/file";
import type { PodFileAccess } from "../src/file/pod";
import { KubernetesClient } from "../src/kubernetes/client";
import { runArgv, type ExecResult, type RunOptions } from "../src/kubernetes/executor";

const readOptions = { maxBytes: 1024, timeoutMs: 1000 };
const target = { namespace: "test", pod: "worker-1", container: "app" };
const success: ExecResult = { ok: true, exitCode: 0, stdout: "", stderr: "", durationMs: 1, timedOut: false, command: [] };

function fixture(exec: PodFileAccess["exec"], key = "cluster-a") {
  let initialized = 0, disposed = 0;
  const access: PodFileAccess = { exec,
    async initialize() { initialized++; }, async dispose() { disposed++; } };
  const provider: ClientProvider<PodFileAccess> = { clientKey: key, createClient: () => access };
  return { provider, get initialized() { return initialized; }, get disposed() { return disposed; } };
}

/** @case id=file-fresh-read,desc=Replacing a mounted file is visible through the same shared client. */
test("local source shares clients, preserves bytes and reopens a replaced path", async () => {
  const directory = await mkdtemp(join(tmpdir(), "file-source-"));
  const manager = new ClientManager();
  try {
    const path = join(directory, "config.yaml");
    const link = join(directory, "mounted.yaml");
    const original = new Uint8Array([0, 255, 128, 10]);
    await writeFile(path, original);
    await symlink(path, link);
    const source: DataSource<FileClient> = new LocalFileDataSource(link, { concurrency: 1 });
    expect(source).toBeInstanceOf(FileDataSource);
    expect("kind" in source).toBe(false);
    expect("id" in source).toBe(false);
    const client = await manager.get(source);
    expect(client).toBe(await manager.get(new LocalFileDataSource(link, { concurrency: 1 })));
    const first = await client.read({ ...readOptions, maxBytes: original.length });
    expect(first).toEqual(original);
    first.fill(1);
    expect(await client.read(readOptions)).toEqual(original);
    await writeFile(join(directory, "next"), "sandbox: new");
    await rename(join(directory, "next"), path);
    expect(new TextDecoder().decode(await client.read(readOptions))).toBe("sandbox: new");
    await manager.dispose();
    await expect(client.read(readOptions)).rejects.toThrow("disposed");
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
});

test("empty files succeed; oversized, missing and directory reads reject without partial content", async () => {
  const directory = await mkdtemp(join(tmpdir(), "file-bounds-"));
  const manager = new ClientManager();
  try {
    const path = join(directory, "config");
    await writeFile(path, "");
    const client = await manager.get(new LocalFileDataSource(path, { concurrency: 2 }));
    expect(await client.read({ ...readOptions, maxBytes: 0 })).toEqual(new Uint8Array());
    await writeFile(path, "你好");
    await expect(client.read({ ...readOptions, maxBytes: 5 })).rejects.toMatchObject({ kind: "limit_exceeded" });
    expect(new TextDecoder().decode(await client.read({ ...readOptions, maxBytes: 6 }))).toBe("你好");
    await rm(path);
    await expect(client.read(readOptions)).rejects.toMatchObject({ kind: "resource_not_found" });
    const folder = await manager.get(new LocalFileDataSource(directory, { concurrency: 1 }));
    await expect(folder.read(readOptions)).rejects.toMatchObject({ kind: "unsupported_operation" });
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
});

test("Pod source snapshots explicit location and shares only the Kubernetes dependency", async () => {
  const calls: unknown[] = [];
  const access = fixture(async (namespace, container, command, options) => {
    calls.push({ namespace, container, command, timeoutMs: options?.timeoutMs, collectStdout: options?.collectStdout });
    const reused = Buffer.from([0, 255]);
    options?.onStdoutBytes?.(reused);
    reused.fill(42);
    options?.onStdoutBytes?.(new Uint8Array([128, 10]));
    return success;
  });
  const mutable = { ...target };
  const options = { concurrency: 2 };
  const path = "/etc/$(touch never); config.yaml";
  const source = new PodFileDataSource(access.provider, mutable, path, options);
  mutable.pod = "mutated"; options.concurrency = 9;
  expect(source.target).toEqual(target);
  expect(source.clientKey).toBe(new PodFileDataSource(access.provider, target, path, { concurrency: 2 }).clientKey);
  for (const changed of [{ ...target, pod: "worker-2" }, { ...target, container: "sidecar" }, { ...target, namespace: "other" }]) {
    expect(new PodFileDataSource(access.provider, changed, path, { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
  }
  expect(new PodFileDataSource(access.provider, target, "/other", { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
  expect(new PodFileDataSource(fixture(async () => success, "cluster-b").provider, target, path, { concurrency: 2 }).clientKey).not.toBe(source.clientKey);
  expect(new PodFileDataSource(access.provider, target, path, { concurrency: 1 }).clientKey).not.toBe(source.clientKey);
  const manager = new ClientManager();
  try {
    const client = await manager.get(source);
    const replica = await manager.get(new PodFileDataSource(access.provider, { ...target, pod: "worker-2" }, path, { concurrency: 2 }));
    expect(access.initialized).toBe(1);
    expect(await client.read({ maxBytes: 4, timeoutMs: 1234 })).toEqual(new Uint8Array([0, 255, 128, 10]));
    expect(calls[0]).toEqual({ namespace: "test", container: target,
      command: ["head", "-c", "5", path], timeoutMs: 1234, collectStdout: false });
    await client.dispose();
    expect(access.disposed).toBe(0);
    expect(await replica.read(readOptions)).toHaveLength(4);
  } finally { await manager.dispose(); }
  expect(access.disposed).toBe(1);
});

test("Pod byte overflow cancels the stream and command failures do not expose stderr", async () => {
  let aborted = false;
  const access = fixture(async (_ns, _target, _command, options) => {
    options?.onStdoutBytes?.(new Uint8Array(5));
    aborted = options!.signal!.aborted;
    return success;
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, "/config", { concurrency: 1 }));
    await expect(client.read({ ...readOptions, maxBytes: 4 })).rejects.toMatchObject({ kind: "limit_exceeded" });
    expect(aborted).toBe(true);
    const failed = fixture(async (_ns, _target, _command, options) => {
      options?.onStdoutBytes?.(new TextEncoder().encode("partial"));
      return { ...success, ok: false, exitCode: 1, stderr: "secret-value" };
    }, "failed-cluster");
    const unreadable = await manager.get(new PodFileDataSource(failed.provider, target, "/config", { concurrency: 1 }));
    const failure = await unreadable.read(readOptions).catch(error => error);
    expect(failure).toBeInstanceOf(FileError);
    expect(failure.kind).toBe("operation_failed");
    expect(failure.message).toContain("test/worker-1/app /config");
    expect(String(failure)).not.toContain("secret-value");
  } finally { await manager.dispose(); }
});

function waitForAbort(options?: RunOptions): Promise<ExecResult> {
  return new Promise((resolve) => {
    const end = () => resolve({ ...success, ok: false });
    if (options?.signal?.aborted) end();
    else options?.signal?.addEventListener("abort", end, { once: true });
  });
}

test("read deadlines include queue time; cancellation does not start queued I/O", async () => {
  let started = 0;
  let entered!: () => void;
  const startedFirst = new Promise<void>(resolve => { entered = resolve; });
  const access = fixture(async (_ns, _target, _command, options) => {
    started++; entered(); return waitForAbort(options);
  });
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, "/config", { concurrency: 1 }));
    const abort = new AbortController();
    const first = client.read({ ...readOptions, signal: abort.signal }).catch(error => error);
    await startedFirst;
    await expect(client.read({ ...readOptions, timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
    const queuedAbort = new AbortController();
    const queued = client.read({ ...readOptions, signal: queuedAbort.signal }).catch(error => error);
    queuedAbort.abort(new Error("caller cancelled"));
    expect((await queued).message).toBe("caller cancelled");
    expect(started).toBe(1);
    abort.abort(new Error("active cancelled"));
    expect((await first).message).toBe("active cancelled");
    await expect(client.read({ ...readOptions, timeoutMs: 20 })).rejects.toMatchObject({ kind: "timeout" });
    expect(started).toBe(2);
    const nextStarted = new Promise<void>(resolve => { entered = resolve; });
    const pending = client.read(readOptions).catch(error => error);
    await nextStarted;
    const waiting = client.read(readOptions).catch(error => error);
    await manager.dispose();
    expect((await pending).message).toContain("disposed");
    expect((await waiting).message).toContain("disposed");
    expect(started).toBe(3);
  } finally { await manager.dispose(); }
});

test("Pod reader works with the real executor byte channel and shell-like paths", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pod-file-exec-"));
  const manager = new ClientManager();
  try {
    const path = join(directory, "config $(never); ' quoted");
    const bytes = new Uint8Array([255, 0, 1, 128, 10]);
    await writeFile(path, bytes);
    const source: ClientProvider<PodFileAccess> = { clientKey: "executor-fixture", createClient: (_clients, signal) =>
      new KubernetesClient({ namespace: "test" }, signal, {
        run: () => { throw new Error("unexpected resource command"); },
        exec: (_target, command, options) => runArgv(command, options),
      }) };
    const client = await manager.get(new PodFileDataSource(source, target, path, { concurrency: 1 }));
    expect(await client.read({ ...readOptions, maxBytes: 5 })).toEqual(bytes);
    await expect(client.read({ ...readOptions, maxBytes: 4 })).rejects.toMatchObject({ kind: "limit_exceeded" });
  } finally { await manager.dispose(); await rm(directory, { recursive: true, force: true }); }
});

test("invalid targets and read limits fail before file access", async () => {
  let reads = 0;
  const access = fixture(async () => { reads++; return success; });
  expect(() => new PodFileDataSource(access.provider, target, "relative", { concurrency: 1 })).toThrow("absolute");
  expect(() => new PodFileDataSource(access.provider, { ...target, container: "" }, "/config", { concurrency: 1 })).toThrow("Container");
  expect(() => new LocalFileDataSource("", { concurrency: 1 })).toThrow("path");
  expect(() => new LocalFileDataSource("/config", { concurrency: 0 })).toThrow("concurrency");
  const manager = new ClientManager();
  try {
    const client = await manager.get(new PodFileDataSource(access.provider, target, "/config", { concurrency: 1 }));
    for (const limits of [{ maxBytes: -1, timeoutMs: 1 }, { maxBytes: 1, timeoutMs: 0 },
      { maxBytes: Number.MAX_SAFE_INTEGER, timeoutMs: 1 }, { maxBytes: 1, timeoutMs: 2 ** 31 }]) {
      await expect(client.read(limits)).rejects.toMatchObject({ kind: "invalid_argument" });
    }
    const abort = new AbortController(); abort.abort(new Error("pre-cancelled"));
    await expect(client.read({ ...readOptions, signal: abort.signal })).rejects.toThrow("pre-cancelled");
    expect(reads).toBe(0);
  } finally { await manager.dispose(); }
});
