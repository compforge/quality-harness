import { expect, test } from "bun:test";
import { createServer } from "node:net";
import { spawn } from "node:child_process";
import { probeConnectivity, type ConnectivityExecutor } from "../src/network/connectivity";

const execute: ConnectivityExecutor = (command, options) => new Promise((resolve, reject) => {
  const process = spawn(command[0]!, command.slice(1), { signal: options.signal, timeout: options.timeoutMs + 500 });
  let output = "";
  process.stdout.on("data", chunk => { output += chunk; });
  process.on("error", reject);
  process.on("close", code => code === 0 ? resolve(output) : reject(new Error(`probe exit ${code}`)));
  process.stdin.end(options.stdin);
});

test("DNS and IP targets connect from the selected executor; refused port is TCP evidence", async () => {
  const server = createServer(socket => socket.end());
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("missing fixture port");
  try {
    for (const host of ["127.0.0.1", "localhost"]) {
      const value = await probeConnectivity({ host, port: address.port }, execute, { timeoutMs: 2000 });
      expect(value.status).toBe("passed");
      expect(value.stages.map(stage => stage.stage)).toEqual(["dns", "tcp"]);
      expect(value.stages[1]!.address).toBe("127.0.0.1");
    }
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
  const value = await probeConnectivity({ host: "127.0.0.1", port: address.port }, execute, { timeoutMs: 2000 });
  expect(value.status).toBe("failed");
  expect(value.stages.at(-1)).toMatchObject({ stage: "tcp", status: "failed" });
});

test("a connected plaintext socket does not count as verified TLS", async () => {
  const server = createServer(socket => socket.end("not TLS"));
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  try {
    const value = await probeConnectivity({ host: "127.0.0.1", port: address.port, tls: true }, execute, { timeoutMs: 2000 });
    expect(value.stages.at(-1)).toMatchObject({ stage: "tls", status: "failed" });
    expect(value.status).toBe("failed");
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});

test("service.namespace is sent unchanged, with bounded execution and no local resolution", async () => {
  let called = false;
  const value = await probeConnectivity({ host: "storage.demo", port: 9000 }, async (command, options) => {
    called = true;
    expect(command[0]).toBe("python3");
    expect(JSON.parse(options.stdin).host).toBe("storage.demo");
    expect(options.timeoutMs).toBe(500);
    return JSON.stringify({ status: "failed", stages: [{ stage: "dns", status: "failed", elapsedMs: 1, error: "gaierror" }] });
  }, { timeoutMs: 500 });
  expect(called).toBe(true);
  expect(value.stages[0]!.stage).toBe("dns");
});

test("tool failures, malformed evidence and cancellation are not connectivity failures", async () => {
  const target = { host: "example.test", port: 443 };
  await expect(probeConnectivity(target, async () => { throw new Error("python missing"); }, { timeoutMs: 100 })).rejects.toThrow("python missing");
  await expect(probeConnectivity(target, async () => '{"status":"passed","stages":[]}', { timeoutMs: 100 })).rejects.toThrow("Invalid connectivity");
  await expect(probeConnectivity(target, async () => { throw new Error("must not execute"); }, { timeoutMs: 100, signal: AbortSignal.abort(new Error("cancelled")) })).rejects.toThrow("cancelled");
  await expect(probeConnectivity({ host: "https://user:secret@example.test", port: 443 }, execute, { timeoutMs: 100 })).rejects.toThrow("hostname/IP");
});
