import { expect, test } from "bun:test";
import { createServer } from "node:http";
import { WebSocketServer, type WebSocket } from "ws";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { KubernetesClient } from "../src/kubernetes/client";

async function fixture(action: (socket: WebSocket, url: URL) => void, concurrency = 1, maxBytes = 1024, timeoutMs = 2000) {
  const root = mkdtempSync(join(tmpdir(), "kubernetes-exec-"));
  const requests: URL[] = [];
  const server = createServer((_request, response) => { response.statusCode = 500; response.end("No resource reads expected"); });
  const ws = new WebSocketServer({ server, verifyClient: ({ req }, done) => {
    done(!req.url?.includes("command=forbidden"), 403);
  } });
  ws.on("connection", (socket, request) => {
    const url = new URL(request.url!, "http://fixture"); requests.push(url); action(socket, url);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No listener");
  const kubeconfig = join(root, "config");
  writeFileSync(kubeconfig, JSON.stringify({ apiVersion: "v1", kind: "Config", "current-context": "fixture",
    contexts: [{ name: "fixture", context: { cluster: "fixture", user: "fixture" } }],
    clusters: [{ name: "fixture", cluster: { server: `http://127.0.0.1:${address.port}` } }], users: [{ name: "fixture", user: {} }] }));
  const client = new KubernetesClient({ kubeconfig, namespace: "demo" }, undefined, undefined, { timeoutMs, concurrency, maxBytes });
  return { client, requests, ws, close: async () => {
    await client.dispose(); for (const socket of ws.clients) socket.terminate();
    await new Promise<void>(resolve => ws.close(() => resolve()));
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(root, { recursive: true, force: true });
  } };
}
function frame(socket: WebSocket, channel: number, text: string | Uint8Array) {
  socket.send(Buffer.concat([Buffer.from([channel]), typeof text === "string" ? Buffer.from(text) : text]));
}
function status(socket: WebSocket, code = 0) {
  frame(socket, 3, JSON.stringify(code === 0 ? { status: "Success" }
    : { status: "Failure", reason: "NonZeroExitCode", details: { causes: [{ reason: "ExitCode", message: String(code) }] } }));
}

test("native exec preserves binary stdout, split UTF-8, stderr and remote exit status without Pod GET", async () => {
  const f = await fixture(socket => {
    const bytes = Buffer.from("你好\0"); frame(socket, 1, bytes.subarray(0, 2)); frame(socket, 1, bytes.subarray(2));
    frame(socket, 2, "warning"); status(socket, 7);
  });
  try {
    const chunks: Uint8Array[] = [];
    const result = await f.client.exec("other", { pod: "pod", container: "app" }, ["printf", "hello"], { onStdoutBytes: bytes => chunks.push(bytes) });
    expect(result).toMatchObject({ ok: false, exitCode: 7, stdout: "你好\0", stderr: "warning", timedOut: false });
    expect(Buffer.concat(chunks).toString()).toBe("你好\0");
    expect(result.command[0]).toBe("kubernetes-api");
    expect(f.requests).toHaveLength(1);
    expect(f.requests[0]!.pathname).toBe("/api/v1/namespaces/other/pods/pod/exec");
    expect(f.requests[0]!.searchParams.getAll("command")).toEqual(["printf", "hello"]);
  } finally { await f.close(); }
});

test("stdin half-close preserves output and completion and streams without retained stdout", async () => {
  const input: Buffer[] = [];
  const f = await fixture(socket => socket.on("message", data => {
    const bytes = Buffer.from(data as Buffer);
    if (bytes[0] === 0) input.push(bytes.subarray(1));
    if (bytes[0] === 255) { frame(socket, 1, "done"); status(socket); }
  }));
  try {
    let output = "";
    const result = await f.client.exec("demo", { pod: "pod" }, ["cat"], {
      stdin: new ReadableStream({ start(controller) { controller.enqueue(Buffer.from("input")); controller.close(); } }),
      collectStdout: false, onStdout: chunk => output += chunk,
    });
    expect(Buffer.concat(input).toString()).toBe("input"); expect(output).toBe("done");
    expect(result).toMatchObject({ ok: true, stdout: "", exitCode: 0 });
  } finally { await f.close(); }
});

test("capacity deadlines never start queued exec and active timeout releases its slot", async () => {
  const f = await fixture((socket, url) => { if (url.searchParams.get("command") === "done") status(socket); });
  try {
    const active = f.client.exec("demo", { pod: "pod" }, ["wait"], { timeoutMs: 150 });
    const queued = f.client.exec("demo", { pod: "pod" }, ["queued"], { timeoutMs: 30 });
    expect((await queued).timedOut).toBe(true); expect((await active).timedOut).toBe(true);
    expect(f.requests).toHaveLength(1);
    expect((await f.client.exec("demo", { pod: "pod" }, ["done"])).ok).toBe(true);
  } finally { await f.close(); }
});

for (const failure of ["limit", "disconnect", "invalid"] as const) test(`exec ${failure} fails without replay`, async () => {
  const f = await fixture(socket => {
    if (failure === "limit") frame(socket, 1, "x".repeat(200));
    if (failure === "disconnect") socket.close();
    if (failure === "invalid") frame(socket, 3, "{}");
  }, 1, 128);
  try {
    await expect(f.client.exec("demo", { pod: "pod" }, ["once"])).rejects.toThrow();
    expect(f.requests).toHaveLength(1);
  } finally { await f.close(); }
});

test("root disposal cancels active and queued sessions and closes sockets", async () => {
  let entered!: () => void;
  const ready = new Promise<void>(resolve => entered = resolve);
  const f = await fixture(() => entered());
  try {
    const active = f.client.exec("demo", { pod: "pod" }, ["wait"]);
    const queued = f.client.exec("demo", { pod: "pod" }, ["queued"]);
    const results = Promise.allSettled([active, queued]);
    await ready; await f.client.dispose();
    expect((await results).map(result => result.status)).toEqual(["rejected", "rejected"]);
    expect(f.requests).toHaveLength(1);
  } finally { await f.close(); }
});


test("rejected handshakes never open or replay a session even when Bun omits the HTTP status", async () => {
  const f = await fixture(() => { throw new Error("Forbidden command must not start"); });
  try {
    await expect(f.client.exec("demo", { pod: "pod" }, ["forbidden"])).rejects.toMatchObject({ kind: "connection_failed" });
    expect(f.requests).toHaveLength(0);
  } finally { await f.close(); }
});

test("explicit command deadlines support longer work than the default and flush decoder tails", async () => {
  const f = await fixture(socket => setTimeout(() => {
    frame(socket, 1, new Uint8Array([0xe4])); status(socket);
  }, 70), 1, 1024, 30);
  try {
    let output = "";
    const result = await f.client.exec("demo", { pod: "pod" }, ["done"], { timeoutMs: 500, onStdout: text => output += text });
    expect(result.ok).toBe(true); expect(output).toBe("\ufffd"); expect(result.stdout).toBe(output);
  } finally { await f.close(); }
});
