import type { Client } from "../client";
import { KubectlExecutor, type Executor, type ExecTarget, type KubectlOptions } from "../kubernetes/executor";
import { startPortForward, type PortForward, type StartPortForward } from "../kubernetes/port-forward";
import type { Endpoint, TcpTransport } from "./index";
import { POD_RELAY_SCRIPT } from "./pod-relay-script";

export interface PodRelayOptions extends KubectlOptions {
  namespace: string;
  /** Restrict eligible existing Pods without coupling callers to the relay runtime. */
  selector?: string;
  startupTimeoutMs: number;
  connectTimeoutMs: number;
  maxConnections: number;
  maxTargets: number;
  maxCandidatePods: number;
  signal?: AbortSignal;
}
interface RelayPod { target: ExecTarget; executable: string }
interface Pending { resolve(value: number): void; reject(error: unknown): void }
interface RawPod {
  metadata?: { name?: string; deletionTimestamp?: string };
  status?: { phase?: string; containerStatuses?: { name: string; ready: boolean }[] };
}

/** Execution-scoped TCP transport borrowed by all Service clients; only its root owner disposes it.
 * Python is private. Requires list pods, pods/exec and pods/portforward, never pods/create.
 */
export class PodRelayTransport implements TcpTransport, Client {
  readonly kind = "tcp";
  readonly name = "pod-relay";
  readonly #controller = new AbortController();
  readonly #signal: AbortSignal;
  readonly #executor: Executor;
  readonly #startForward: StartPortForward;
  readonly #targets = new Map<string, Promise<Endpoint>>();
  readonly #pending = new Map<number, Pending>();
  readonly #forwards: PortForward[] = [];
  #startup?: Promise<RelayPod>;
  #execution?: Promise<unknown>;
  #input?: ReadableStreamDefaultController<Uint8Array>;
  #heartbeat?: ReturnType<typeof setInterval>;
  #failure?: Error;
  #disposal?: Promise<void>;
  #sequence = 0;

  constructor(private readonly options: PodRelayOptions, dependencies?: { executor?: Executor; startForward?: StartPortForward }) {
    for (const key of ["startupTimeoutMs", "connectTimeoutMs", "maxConnections", "maxTargets", "maxCandidatePods"] as const) {
      if (!Number.isSafeInteger(options[key]) || options[key] <= 0) throw new Error(`${key} must be a positive integer`);
    }
    this.#executor = dependencies?.executor ?? new KubectlExecutor(options);
    this.#startForward = dependencies?.startForward ?? startPortForward;
    this.#signal = options.signal ? AbortSignal.any([options.signal, this.#controller.signal]) : this.#controller.signal;
    this.#signal.addEventListener("abort", this.#onAbort, { once: true });
  }
  async initialize(): Promise<void> { this.#check(); }
  #check(): void { this.#signal.throwIfAborted(); if (this.#failure) throw this.#failure; }
  #onAbort = () => { this.#fail(new Error("Pod relay aborted")); };
  #fail(error: Error): void {
    this.#failure ??= error;
    if (this.#heartbeat) clearInterval(this.#heartbeat);
    if (this.#input) { try { this.#input.close(); } catch { /* exec may already have closed stdin */ } }
    this.#input = undefined;
    for (const pending of this.#pending.values()) pending.reject(this.#failure);
    this.#pending.clear();
    for (const forward of this.#forwards.splice(0)) forward.stop();
  }
  async #select(): Promise<RelayPod> {
    const command = ["get", "pods", "--field-selector=status.phase=Running", "-o", "json"];
    if (this.options.selector) command.push("-l", this.options.selector);
    const listed = await this.#executor.run(command, { timeoutMs: this.options.startupTimeoutMs, signal: this.#signal });
    if (!listed.ok) throw new Error("Cannot discover an existing Pod for relay");
    const pods = (JSON.parse(listed.stdout) as { items: RawPod[] }).items
      .filter(pod => pod.status?.phase === "Running" && !pod.metadata?.deletionTimestamp)
      .sort((a, b) => (a.metadata?.name ?? "").localeCompare(b.metadata?.name ?? ""))
      .slice(0, this.options.maxCandidatePods);
    for (const pod of pods) {
      if (!pod.metadata?.name) continue;
      for (const container of pod.status?.containerStatuses ?? []) {
        if (!container.ready) continue;
        const target = { pod: pod.metadata.name, container: container.name };
        for (const executable of ["python3", "python"]) {
          this.#check();
          const probe = await this.#executor.exec(target,
            [executable, "-c", "import asyncio,sys; assert sys.version_info >= (3, 8); print('relay-ready')"],
            { timeoutMs: this.options.startupTimeoutMs, signal: this.#signal });
          if (probe.ok && probe.stdout.trim() === "relay-ready") return { target, executable };
        }
      }
    }
    throw new Error("No eligible existing Pod can run the TCP relay");
  }
  async #start(): Promise<RelayPod> {
    const pod = await this.#select();
    this.#check();
    const input = new ReadableStream<Uint8Array>({ start: controller => { this.#input = controller; } });
    let ready!: () => void;
    let rejectReady!: (error: unknown) => void;
    const readiness = new Promise<void>((resolve, reject) => { ready = resolve; rejectReady = reject; });
    let buffered = "";
    const config = { connectTimeoutMs: this.options.connectTimeoutMs, maxConnections: this.options.maxConnections,
      maxTargets: this.options.maxTargets, leaseMs: 30_000 };
    this.#execution = this.#executor.exec(pod.target,
      [pod.executable, "-u", "-c", POD_RELAY_SCRIPT, JSON.stringify(config)], {
        stdin: input, signal: this.#signal, timeoutMs: 2_147_483_647, collectStdout: false,
        onStdout: chunk => {
          buffered += chunk;
          if (buffered.length > 65_536) { this.#fail(new Error("Pod relay control response exceeded limit")); return; }
          while (buffered.includes("\n")) {
            const end = buffered.indexOf("\n");
            const line = buffered.slice(0, end); buffered = buffered.slice(end + 1);
            try {
              const response = JSON.parse(line) as { ready?: boolean; id?: number; port?: number };
              if (response.ready) ready();
              else if (response.id !== undefined) {
                const pending = this.#pending.get(response.id);
                this.#pending.delete(response.id);
                if (Number.isInteger(response.port) && response.port! > 0 && response.port! <= 65535) pending?.resolve(response.port!);
                else pending?.reject(new Error("Pod relay listener unavailable"));
              }
            } catch { this.#fail(new Error("Invalid Pod relay control response")); }
          }
        },
      }).then(() => {
        const error = new Error("Pod relay execution ended"); rejectReady(error); this.#fail(error);
      }, () => {
        const error = new Error("Pod relay execution failed"); rejectReady(error); this.#fail(error);
      });
    // Lease expiry bounds remote cleanup when the API connection disappears without EOF.
    this.#heartbeat = setInterval(() => {
      try { this.#send({ heartbeat: true }); } catch { this.#fail(new Error("Pod relay control channel closed")); }
    }, 5_000);
    const timer = setTimeout(() => rejectReady(new Error("Pod relay startup timed out")), this.options.startupTimeoutMs);
    try { await readiness; this.#check(); return pod; } finally { clearTimeout(timer); }
  }
  #send(value: unknown): void {
    this.#check();
    if (!this.#input) throw new Error("Pod relay control channel unavailable");
    this.#input.enqueue(new TextEncoder().encode(`${JSON.stringify(value)}\n`));
  }
  connect(endpoint: Endpoint): Promise<Endpoint> {
    this.#check();
    if (!endpoint.host || !Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535) {
      return Promise.reject(new Error("Invalid relay endpoint"));
    }
    const key = JSON.stringify([endpoint.host, endpoint.port]);
    let pending = this.#targets.get(key);
    if (!pending) {
      if (this.#targets.size >= this.options.maxTargets) return Promise.reject(new Error("Pod relay target limit reached"));
      this.#startup ??= this.#start().catch(error => { this.#fail(error instanceof Error ? error : new Error("Pod relay startup failed")); throw error; });
      pending = this.#connect(endpoint, this.#startup);
      this.#targets.set(key, pending);
    }
    return pending.then(local => ({ ...local, servername: endpoint.servername ?? endpoint.host }));
  }
  async #connect(endpoint: Endpoint, startup: Promise<RelayPod>): Promise<Endpoint> {
    const pod = await startup;
    this.#check();
    const id = ++this.#sequence;
    const remotePort = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id); reject(new Error("Pod relay listener timed out"));
      }, this.options.startupTimeoutMs);
      this.#pending.set(id, {
        resolve: value => { clearTimeout(timer); resolve(value); },
        reject: error => { clearTimeout(timer); reject(error); },
      });
      try { this.#send({ id, host: endpoint.host, port: endpoint.port }); }
      catch (error) { this.#pending.get(id)?.reject(error); this.#pending.delete(id); }
    });
    this.#check();
    const forwarded = await this.#startForward({ ...this.options, target: { kind: "pod", name: pod.target.pod },
      remotePort, timeoutMs: this.options.startupTimeoutMs });
    if (!forwarded.ok || !forwarded.value) throw new Error("Pod relay port-forward unavailable");
    try { this.#check(); } catch (error) { forwarded.value.stop(); throw error; }
    this.#forwards.push(forwarded.value);
    return { host: "127.0.0.1", port: forwarded.value.localPort };
  }
  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("Pod relay disposed"));
      this.#fail(new Error("Pod relay disposed"));
      await Promise.allSettled(this.#targets.values());
      await this.#execution;
      this.#signal.removeEventListener("abort", this.#onAbort);
      this.#targets.clear();
    })();
  }
}
