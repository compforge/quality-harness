import { KubeConfig } from "@kubernetes/client-node";
import WebSocket from "ws";
import type { RequestOptions } from "node:https";
import type { Agent } from "node:http";
import type { ReadableStreamDefaultReader } from "node:stream/web";
import { ConcurrencyPool } from "../concurrency";
import { KubernetesError, kubernetesHttpError } from "../errors";
import type { ExecTarget, ExecResult, RunOptions } from "./executor";
import type { KubernetesConnectionOptions } from "./resource-transport";

export interface KubernetesExecLimits { timeoutMs: number; concurrency: number; maxBytes: number }

/**
 * @spec Each exec owns one channel session; it is never retried or replayed.
 * @rule The root owns configuration and capacity. Deadlines cover queueing, auth, upgrade and execution.
 * @why A WebSocket upgrade cannot reuse an ordinary HTTP request connection as another exec session.
 */
export class KubernetesExecTransport {
  readonly #config = new KubeConfig();
  readonly #capacity: ConcurrencyPool;
  readonly #server: string;

  constructor(options: KubernetesConnectionOptions, private readonly signal: AbortSignal,
    private readonly limits: KubernetesExecLimits) {
    if (!Object.values(limits).every(value => Number.isSafeInteger(value) && value > 0)) {
      throw new KubernetesError("Exec limits must be positive integers", { kind: "invalid_argument" });
    }
    if (options.kubeconfig) this.#config.loadFromFile(options.kubeconfig);
    else this.#config.loadFromDefault();
    if (options.context) this.#config.setCurrentContext(options.context);
    const cluster = this.#config.getCurrentCluster();
    if (!cluster) throw new KubernetesError("Kubernetes context has no cluster", { kind: "invalid_argument" });
    // Bun's proxy WebSocket retains sockets after terminate and cannot preserve all TLS overrides.
    if (process.versions.bun && cluster.proxyUrl) {
      throw new KubernetesError("Native exec through a kubeconfig proxy requires a Node host", { kind: "unsupported_operation" });
    }
    this.#server = cluster.server.replace(/\/$/, "");
    if (new URL(cluster.server).protocol === "http:") this.#config.clusters = this.#config.clusters.map(
      item => item === cluster ? { ...item, skipTLSVerify: true } : item);
    this.#capacity = new ConcurrencyPool(limits.concurrency);
  }

  async exec(namespace: string, target: ExecTarget, command: string[], options: RunOptions = {}): Promise<ExecResult> {
    const started = performance.now();
    const timeoutMs = options.timeoutMs ?? this.limits.timeoutMs;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || !namespace || !target.pod || !command.length) {
      throw new KubernetesError("Exec requires a namespace, Pod, command and positive timeout", { kind: "invalid_argument" });
    }
    const caller = options.signal ? AbortSignal.any([this.signal, options.signal]) : this.signal;
    const deadline = new AbortController();
    const signal = AbortSignal.any([caller, deadline.signal]);
    const timer = setTimeout(() => deadline.abort(), timeoutMs);
    try {
      return await this.#capacity.run(() => this.#session(namespace, target, command, options, signal, started, deadline.signal), signal);
    } catch (error) {
      if (caller.aborted) throw caller.reason;
      if (deadline.signal.aborted) return this.#result(namespace, target, command, started, null, "", "Exec deadline exceeded", true);
      throw error;
    } finally { clearTimeout(timer); }
  }

  #result(namespace: string, target: ExecTarget, command: string[], started: number,
    exitCode: number | null, stdout: string, stderr: string, timedOut = false): ExecResult {
    return { ok: exitCode === 0 && !timedOut, exitCode, stdout, stderr, timedOut, durationMs: performance.now() - started,
      command: ["kubernetes-api", "exec", "-n", namespace, target.pod,
        ...(target.container ? ["-c", target.container] : []), "--", ...command] };
  }

  #session(namespace: string, target: ExecTarget, command: string[], options: RunOptions,
    signal: AbortSignal, started: number, deadline: AbortSignal): Promise<ExecResult> {
    return new Promise((resolve, reject) => {
      let socket: WebSocket | undefined;
      let agent: Agent | undefined;
      let input: ReadableStreamDefaultReader<Uint8Array> | undefined;
      let settled = false;
      let stdout = "", stderr = "", retained = 0;
      const outDecoder = new TextDecoder(), errDecoder = new TextDecoder();
      const finish = (exitCode: number | null, error?: Error) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", abort);
        socket?.terminate();
        agent?.destroy();
        void input?.cancel().catch(() => {});
        const tail = outDecoder.decode(), errTail = errDecoder.decode();
        if (options.collectStdout !== false) stdout += tail;
        stderr += errTail;
        try {
          if (tail) options.onStdout?.(tail);
          if (errTail) options.onStderr?.(errTail);
        } catch (cause) { error ??= cause instanceof Error ? cause : new Error("Exec output callback failed"); }
        if (error) reject(error);
        else resolve(this.#result(namespace, target, command, started, exitCode, stdout, stderr, deadline.aborted));
      };
      const abort = () => {
        if (deadline.aborted) { stderr += "\nExec deadline exceeded"; finish(null); }
        else finish(null, signal.reason instanceof Error ? signal.reason : new Error("Exec aborted"));
      };
      const fail = (kind: "connection_failed" | "connection_lost" | "invalid_response" | "limit_exceeded", cause?: unknown) =>
        finish(null, new KubernetesError(`Kubernetes exec ${namespace}/${target.pod} failed`, { kind, cause }));
      if (signal.aborted) { abort(); return; }
      signal.addEventListener("abort", abort, { once: true });
      const request: RequestOptions = { headers: {} };
      void this.#config.applyToHTTPSOptions(request).then(() => {
        agent = request.agent as Agent;
        if (settled || signal.aborted) { agent?.destroy(); return; }
        const url = new URL(`${this.#server}/api/v1/namespaces/${encodeURIComponent(namespace)}/pods/${encodeURIComponent(target.pod)}/exec`);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        for (const arg of command) url.searchParams.append("command", arg);
        url.searchParams.set("stdout", "true"); url.searchParams.set("stderr", "true");
        url.searchParams.set("stdin", String(options.stdin !== undefined)); url.searchParams.set("tty", "false");
        if (target.container) url.searchParams.set("container", target.container);
        // v5 supports closing stdin without terminating stdout/status. Older sessions are safe only without stdin.
        socket = new WebSocket(url, options.stdin === undefined ? ["v5.channel.k8s.io", "v4.channel.k8s.io"] : ["v5.channel.k8s.io"],
          { ...request, ...(process.versions.bun ? { tls: { ca: request.ca, cert: request.cert, key: request.key, pfx: request.pfx,
                passphrase: request.passphrase, rejectUnauthorized: request.rejectUnauthorized, serverName: request.servername } } : {}), maxPayload: this.limits.maxBytes, handshakeTimeout: this.limits.timeoutMs });
        // Bun's ws shim does not expose upgrade responses; preserve an unknown connection failure there.
        if (!process.versions.bun) socket.on("unexpected-response", (request, response) => {
          const status = response.statusCode ?? 500;
          response.destroy(); request.destroy();
          const failure = kubernetesHttpError(status);
          finish(null, new KubernetesError(`Kubernetes exec ${namespace}/${target.pod} HTTP ${status}`, {
            kind: failure.kind, code: status,
          }));
        });
        socket.on("error", error => fail("connection_failed", error));
        socket.on("close", () => { if (!settled) fail("connection_lost"); });
        socket.on("message", (data, binary) => {
          if (settled) return;
          try {
            if (!binary) throw new Error("Exec frame must be binary");
            const frame = Buffer.isBuffer(data) ? data : Array.isArray(data) ? Buffer.concat(data) : Buffer.from(data);
            if (!frame.length) throw new Error("Empty exec frame");
            if (frame.length > this.limits.maxBytes) { fail("limit_exceeded"); return; }
            const channel = frame[0], payload = frame.subarray(1);
            if (channel === 255) {
              if (payload.length !== 1 || ![0, 1, 2].includes(payload[0]!)) throw new Error("Invalid channel close");
              return;
            }
            if (channel === 3) {
              const status = JSON.parse(payload.toString("utf8"));
              if (status.status === "Success") { finish(0); return; }
              const code = status.details?.causes?.find((cause: { reason?: string }) => cause.reason === "ExitCode")?.message;
              if (status.status !== "Failure" || !/^\d+$/.test(code ?? "")) throw new Error("Exec status lacks exit code");
              if (!Number.isSafeInteger(Number(code))) throw new Error("Invalid exit code");
              finish(Number(code)); return;
            }
            if (channel !== 1 && channel !== 2) throw new Error("Invalid exec channel");
            if (channel === 2 || options.collectStdout !== false) retained += payload.length;
            if (retained > this.limits.maxBytes) { fail("limit_exceeded"); return; }
            if (channel === 1) {
              options.onStdoutBytes?.(payload);
              const text = outDecoder.decode(payload, { stream: true });
              if (options.collectStdout !== false) stdout += text;
              options.onStdout?.(text);
            } else {
              const text = errDecoder.decode(payload, { stream: true }); stderr += text; options.onStderr?.(text);
            }
          } catch (error) { fail("invalid_response", error); }
        });
        socket.on("open", () => {
          if (options.stdin === undefined || settled) return;
          const send = (bytes: Uint8Array) => new Promise<void>((done, failed) => {
            if (settled) { failed(new Error("Exec closed")); return; }
            socket!.send(Buffer.concat([Buffer.from([0]), bytes]), error => error ? failed(error) : done());
          });
          void (async () => {
            const stdin = options.stdin!;
            if (typeof stdin === "string" || stdin instanceof Uint8Array) await send(typeof stdin === "string" ? new TextEncoder().encode(stdin) : stdin);
            else {
              const reader = stdin.getReader();
              input = reader;
              while (!settled) { const chunk = await reader.read(); if (chunk.done) break; await send(chunk.value); }
            }
            if (!settled) socket!.send(Buffer.from([255, 0]));
          })().catch(error => { if (!settled) fail("connection_failed", error); });
        });
      }).catch(error => { if (!settled) fail("connection_failed", error); });
    });
  }
}
