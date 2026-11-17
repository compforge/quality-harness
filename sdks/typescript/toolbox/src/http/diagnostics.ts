import { AsyncLocalStorage } from "node:async_hooks";
import { channel } from "node:diagnostics_channel";
import type { ClientRequest, Agent } from "node:http";
import type { Socket } from "node:net";
import type { EventEmitter } from "node:events";
import type { HttpLimits } from "./types";

/** Timings are observed offsets from operation start, not inferred server-side stages. */
export interface HttpRequestDiagnostics {
  phase: "queued" | "preparing" | "dispatch" | "socket_assigned" | "connected" | "tls_ready" | "request_sent" | "response_headers";
  elapsedMs: number;
  connectTimeoutMs: number;
  requestTimeoutMs: number;
  timeoutSource?: "operation" | "http_handler";
  request?: { method: string; host: string; port: number; protocol: string; dialHost: string; dialPort: number };
  nativeRequestObserved: boolean;
  timings: Partial<Record<"dequeued" | "dispatched" | "socket" | "lookup" | "connected" | "tls" | "sent" | "headers", number>>;
  socket?: { reused: boolean; remoteAddress?: string; remotePort?: number; resolvedAddress?: string };
  causeCodes: string[];
}

const active = new AsyncLocalStorage<HttpRequestTrace>();
const failures = new WeakMap<object, HttpRequestDiagnostics>();
const channels = [channel("http.client.request.created"), channel("http.client.request.start")];
let observers = 0;
const observe = (message: unknown) => {
  if (message && typeof message === "object" && "request" in message) active.getStore()?.attach(message.request as ClientRequest);
};
const isObject = (value: unknown): value is object => (typeof value === "object" && value !== null) || typeof value === "function";

/** Only safe codes cross the evidence boundary; native messages and request material stay private. */
function causeCodes(error: unknown): string[] {
  const codes = new Set<string>();
  const seen = new Set<object>();
  while (isObject(error) && !seen.has(error) && seen.size < 6) {
    seen.add(error);
    const item = error as { code?: unknown; name?: unknown; cause?: unknown };
    if (typeof item.code === "string" && /^[A-Za-z0-9_]{1,80}$/.test(item.code)) codes.add(item.code);
    else if (item.name === "TimeoutError" || item.name === "AbortError") codes.add(item.name);
    error = item.cause;
  }
  return [...codes];
}

export function httpRequestDiagnostics(error: unknown): HttpRequestDiagnostics | undefined {
  const seen = new Set<object>();
  while (isObject(error) && !seen.has(error) && seen.size < 6) {
    seen.add(error);
    const found = failures.get(error);
    if (found) return structuredClone(found);
    error = (error as { cause?: unknown }).cause;
  }
}

/**
 * @spec Observe the existing handler and socket; never dial, replay, mutate signing or retain URLs/headers.
 * @why Missing native events remain unknown; a separate network probe cannot explain this request.
 * @link ../../../../../docs/toolbox.md#s3
 */
export class HttpRequestTrace {
  static current(): HttpRequestTrace | undefined { return active.getStore(); }
  readonly #started = performance.now();
  readonly #timings: HttpRequestDiagnostics["timings"] = {};
  readonly #remove: Array<() => void> = [];
  readonly #requests = new WeakSet<ClientRequest>();
  #agent?: Agent;
  #request?: HttpRequestDiagnostics["request"];
  #socket?: HttpRequestDiagnostics["socket"];
  #native = false;
  constructor(private readonly limits: Pick<HttpLimits, "connectTimeoutMs" | "requestTimeoutMs">) {}
  #elapsed(): number { return Math.round((performance.now() - this.#started) * 1000) / 1000; }
  #mark(name: keyof HttpRequestDiagnostics["timings"]): void { this.#timings[name] ??= this.#elapsed(); }
  dequeued(): void { this.#mark("dequeued"); }
  dispatch(request: { method: string; hostname: string; port?: number; protocol: string }, route: { host: string; port: number }, agent: Agent): void {
    this.#agent = agent;
    this.#request = { method: request.method, host: request.hostname, port: request.port ?? (request.protocol === "https:" ? 443 : 80),
      protocol: request.protocol, dialHost: route.host, dialPort: route.port };
    this.#mark("dispatched");
  }
  headers(): void { this.#mark("headers"); }
  #listen<Args extends unknown[]>(emitter: EventEmitter, event: string, listener: (...args: Args) => void): void {
    emitter.on(event, listener);
    this.#remove.push(() => emitter.removeListener(event, listener));
  }
  attach(request: ClientRequest): void {
    if (!request || (request as ClientRequest & { agent?: Agent }).agent !== this.#agent || !this.#agent || this.#requests.has(request)) return;
    this.#requests.add(request);
    this.#native = true;
    const socket = (value: Socket) => {
      this.#mark("socket");
      this.#socket = { reused: request.reusedSocket === true };
      const connected = () => {
        this.#mark("connected");
        if (this.#socket) Object.assign(this.#socket, { remoteAddress: value.remoteAddress, remotePort: value.remotePort });
      };
      if (!value.connecting && value.remoteAddress) connected();
      this.#listen(value, "connect", connected);
      this.#listen(value, "secureConnect", () => this.#mark("tls"));
      this.#listen(value, "lookup", (error: unknown, address: unknown) => {
        this.#mark("lookup");
        if (!error && typeof address === "string" && this.#socket) this.#socket.resolvedAddress = address;
      });
    };
    if (request.socket) socket(request.socket);
    else this.#listen(request, "socket", socket);
    if (request.writableFinished) this.#mark("sent");
    this.#listen(request, "finish", () => this.#mark("sent"));
    this.#listen(request, "response", () => this.headers());
  }
  capture(error: unknown, timeoutSource?: HttpRequestDiagnostics["timeoutSource"]): void {
    if (!isObject(error)) return;
    const timings = this.#timings;
    const phase = timings.headers !== undefined ? "response_headers"
      : timings.sent !== undefined ? "request_sent"
      : timings.tls !== undefined ? "tls_ready"
      : timings.connected !== undefined ? "connected"
      : timings.socket !== undefined ? "socket_assigned"
      : timings.dispatched !== undefined ? "dispatch"
      : timings.dequeued !== undefined ? "preparing" : "queued";
    failures.set(error, structuredClone({
      phase,
      elapsedMs: this.#elapsed(),
      connectTimeoutMs: this.limits.connectTimeoutMs,
      requestTimeoutMs: this.limits.requestTimeoutMs,
      timeoutSource,
      request: this.#request,
      nativeRequestObserved: this.#native,
      timings,
      socket: this.#socket,
      causeCodes: causeCodes(error),
    }));
  }
  async run<T>(work: () => Promise<T>): Promise<T> {
    if (observers++ === 0) for (const item of channels) item.subscribe(observe);
    try {
      return await active.run(this, work);
    } finally {
      for (const remove of this.#remove) remove();
      if (--observers === 0) for (const item of channels) item.unsubscribe(observe);
    }
  }
}
