import { expect, test } from "bun:test";
import { createConnection, createServer, type Server } from "node:net";
import { once } from "node:events";
import { ClientManager } from "../src/client-manager";
import { PodRelayTransport, type PodRelayOptions } from "../src/transport";
import { runArgv, type Executor, type ExecResult } from "../src/kubernetes/executor";
import type { StartPortForward } from "../src/kubernetes/port-forward";
import { POD_RELAY_SCRIPT } from "../src/transport/pod-relay-script";

const options: PodRelayOptions = { namespace: "test", startupTimeoutMs: 3000, connectTimeoutMs: 1000,
  maxConnections: 4, maxTargets: 4, maxCandidatePods: 4 };
const ok = (stdout = ""): ExecResult => ({ ok: true, stdout, stderr: "", exitCode: 0, durationMs: 0, timedOut: false, command: [] });
function fixture(overrides: Partial<PodRelayOptions> = {}) {
  let discoveries = 0, probes = 0, launches = 0, forwards = 0, stops = 0;
  const remote = new AbortController();
  const executor: Executor = {
    run: async command => {
      discoveries++;
      expect(command.slice(0, 2)).toEqual(["get", "pods"]);
      return ok(JSON.stringify({ items: [{ metadata: { name: "helper" }, status: { phase: "Running",
        containerStatuses: [{ name: "app", ready: true }] } }] }));
    },
    exec: async (target, command, opts) => {
      expect(target).toEqual({ pod: "helper", container: "app" });
      if (!command.includes(POD_RELAY_SCRIPT)) { probes++; return ok("relay-ready\n"); }
      launches++;
      return runArgv(command, { ...opts, signal: opts?.signal ? AbortSignal.any([opts.signal, remote.signal]) : remote.signal });
    },
  };
  const startForward: StartPortForward = async args => {
    forwards++;
    expect(args.target).toEqual({ kind: "pod", name: "helper" });
    return { ok: true, value: { target: args.target!, localPort: args.remotePort, command: [], stop: () => { stops++; } } };
  };
  return { relay: new PodRelayTransport({ ...options, ...overrides }, { executor, startForward }), executor,
    remote, stats: () => ({ discoveries, probes, launches, forwards, stops }) };
}
async function listen(server: Server): Promise<number> {
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  return (server.address() as { port: number }).port;
}
async function exchange(port: number, payload: Buffer): Promise<Buffer> {
  const socket = createConnection({ host: "127.0.0.1", port });
  socket.setTimeout(3000, () => socket.destroy(new Error("test socket timed out")));
  const chunks: Buffer[] = [];
  let size = 0;
  const response = new Promise<Buffer>((resolve, reject) => {
    socket.on("error", reject);
    socket.on("data", chunk => {
      chunks.push(chunk); size += chunk.length;
      if (size === payload.length) resolve(Buffer.concat(chunks));
    });
  });
  try { await once(socket, "connect"); socket.write(payload); return await response; }
  finally { socket.destroy(); }
}

test("one execution shares one relay across services and coalesces target initialization", async () => {
  const a = createServer(socket => socket.pipe(socket));
  const b = createServer(socket => socket.pipe(socket));
  const [portA, portB] = await Promise.all([listen(a), listen(b)]);
  const f = fixture();
  const clients = new ClientManager();
  let factories = 0;
  const source = { clientKey: "transport:cluster-a:test", createClient: () => { factories++; return f.relay; } };
  try {
    const [chat, hibot] = await Promise.all([clients.get(source), clients.get(source)]);
    expect(chat).toBe(hibot);
    const [first, same, second] = await Promise.all([
      chat.connect({ host: "127.0.0.1", port: portA }), hibot.connect({ host: "127.0.0.1", port: portA }),
      hibot.connect({ host: "127.0.0.1", port: portB }),
    ]);
    expect(first).toEqual(same);
    const payload = Buffer.alloc(1024 * 1024, 0xa7);
    const echoed = await exchange(first.port, payload);
    expect(echoed.length).toBe(payload.length);
    expect(echoed.equals(payload)).toBe(true);
    expect(await exchange(second.port, Buffer.from("another db"))).toEqual(Buffer.from("another db"));
    expect(factories).toBe(1);
    expect(f.stats()).toEqual({ discoveries: 1, probes: 1, launches: 1, forwards: 2, stops: 0 });
  } finally { await clients.dispose(); a.close(); b.close(); }
  expect(f.stats().stops).toBe(2);
  await clients.dispose();
  expect(f.stats().stops).toBe(2);
  expect(() => f.relay.connect({ host: "127.0.0.1", port: portA })).toThrow();
});

test("target budget is shared across callers and input stays out of command strings", async () => {
  const f = fixture({ maxTargets: 1 });
  try {
    await f.relay.connect({ host: "not-a-shell-$(echo-test)", port: 3306 });
    await expect(f.relay.connect({ host: "second", port: 3306 })).rejects.toThrow("target limit");
  } finally { await f.relay.dispose(); }
});

test("relay process death invalidates all cached endpoints and closes forwards", async () => {
  const f = fixture();
  await f.relay.connect({ host: "localhost", port: 3306 });
  f.remote.abort();
  for (let i = 0; i < 100 && f.stats().stops === 0; i++) await new Promise(resolve => setTimeout(resolve, 10));
  expect(f.stats().stops).toBe(1);
  expect(() => f.relay.connect({ host: "localhost", port: 3306 })).toThrow("execution ended");
  await f.relay.dispose();
});

test("missing eligible runtime fails without creating a Pod", async () => {
  const f = fixture();
  const executor: Executor = { ...f.executor, exec: async () => ({ ...ok(), ok: false, exitCode: 127 }) };
  const relay = new PodRelayTransport(options, { executor });
  try { await expect(relay.connect({ host: "db", port: 3306 })).rejects.toThrow("No eligible"); }
  finally { await relay.dispose(); await f.relay.dispose(); }
});

test("dispose during pending forward stops a late forward before returning it", async () => {
  const f = fixture();
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  let finish!: () => void;
  let stopped = 0;
  const forward: StartPortForward = async args => {
    entered(); await new Promise<void>(resolve => { finish = resolve; });
    return { ok: true, value: { target: args.target!, localPort: args.remotePort, command: [], stop: () => { stopped++; } } };
  };
  const relay = new PodRelayTransport(options, { executor: f.executor, startForward: forward });
  const connection = relay.connect({ host: "localhost", port: 3306 });
  const rejected = connection.catch(error => error);
  await started;
  const disposal = relay.dispose();
  finish();
  const [error] = await Promise.all([rejected, disposal]);
  expect(error).toBeInstanceOf(Error);
  expect(stopped).toBe(1);
  await f.relay.dispose();
});

test("remote lease expires without heartbeat even while stdin remains open", async () => {
  const input = new ReadableStream<Uint8Array>({});
  const result = await runArgv(["python3", "-u", "-c", POD_RELAY_SCRIPT,
    JSON.stringify({ maxConnections: 1, maxTargets: 1, connectTimeoutMs: 100, leaseMs: 100 })],
    { stdin: input, timeoutMs: 3000 });
  expect(result.ok).toBe(true);
  expect(result.stdout).toContain('"ready": true');
  expect(result.durationMs).toBeLessThan(2000);
});

test("Node runtime preserves TCP half-close and the complete reverse response", async () => {
  // Bun's net.Server half-close differs from Node; exercise the supported distribution runtime.
  const transport = new URL('../dist/transport/index.js', import.meta.url).href;
  const executor = new URL('../dist/kubernetes/executor.js', import.meta.url).href;
  const result = await runArgv(['node', '--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {createServer,createConnection} from 'node:net';
    import {once} from 'node:events';
    import {PodRelayTransport} from ${JSON.stringify(transport)};
    import {runArgv} from ${JSON.stringify(executor)};
    const server=createServer({allowHalfOpen:true}, socket=>{
      const chunks=[];socket.on('data',x=>chunks.push(x));
      socket.on('end',()=>socket.end(Buffer.concat(chunks)));
    });
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const relay=new PodRelayTransport({namespace:'test',startupTimeoutMs:3000,connectTimeoutMs:1000,
      maxConnections:2,maxTargets:2,maxCandidatePods:1},{executor:{
        run:async()=>({ok:true,stdout:JSON.stringify({items:[{metadata:{name:'a'},status:{phase:'Running',
          containerStatuses:[{name:'a',ready:true}]}}]})}),
        exec:async(t,c,o)=>runArgv(c,o)
      },startForward:async a=>({ok:true,value:{target:a.target,localPort:a.remotePort,command:[],stop(){}}})});
    let socket;
    try {
      const address=await relay.connect({host:'127.0.0.1',port:server.address().port});
      socket=createConnection(address);
      socket.setTimeout(3000,()=>socket.destroy(new Error('test timeout')));
      const chunks=[];socket.on('data',x=>chunks.push(x));
      const done=once(socket,'end');await once(socket,'connect');
      const payload=Buffer.alloc(1048576,167);socket.end(payload);await done;
      assert.ok(Buffer.concat(chunks).equals(payload));
    } finally {socket?.destroy();await relay.dispose();server.close();}
  `], { timeoutMs: 5000 });
  expect(result.stderr).toBe('');
  expect(result.ok).toBe(true);
});
