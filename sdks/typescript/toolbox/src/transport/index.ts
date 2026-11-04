import { Transport } from "./lifecycle";
import type { ConnectionAttempt } from "../connection/acquire";
export { Transport } from "./lifecycle";

/** Dial address only. Protocol identity remains with the client, never inferred from a forwarded address. */
export interface Endpoint { host: string; port: number; servername?: string }

export interface TcpTransport {
  readonly kind: "tcp";
  readonly name: string;
  connect(endpoint: Endpoint, attempt?: ConnectionAttempt): Promise<Endpoint>;
}

export class DirectTransport extends Transport implements TcpTransport {
  readonly kind = "tcp";
  readonly name = "direct";
  protected override paths(): readonly TransportPath[] { return [this]; }
  async connect(endpoint: Endpoint, attempt?: ConnectionAttempt): Promise<Endpoint> {
    this.signal.throwIfAborted(); attempt?.signal.throwIfAborted(); return endpoint;
  }
}

/** The supplied forwarder owns port allocation and cleanup; external endpoints may stay direct. */
export class PortForwardTransport extends Transport implements TcpTransport {
  readonly kind = "tcp";
  readonly name = "port-forward";
  constructor(private readonly forward: (endpoint: Endpoint, attempt?: ConnectionAttempt) => Promise<Endpoint>,
    private readonly release?: () => void | Promise<void>, signal?: AbortSignal) { super([], signal); }
  protected override paths(): readonly TransportPath[] { return [this]; }
  connect(endpoint: Endpoint, attempt?: ConnectionAttempt): Promise<Endpoint> {
    this.signal.throwIfAborted(); attempt?.signal.throwIfAborted(); return this.forward(endpoint, attempt);
  }
  protected override async close(): Promise<void> { await this.release?.(); }
}

export interface PodRunOptions { stdin: string; timeoutMs: number }
export type PodRunner = (command: readonly string[], options: PodRunOptions) => Promise<string>;

/** A Python-capable Pod path. Protocol code supplies the script; the host enforces access. */
export class PodPythonTransport extends Transport {
  readonly kind = "python";
  readonly name = "pod-python";
  constructor(private readonly exec: PodRunner, private readonly python = "python") { super(); }
  protected override paths(): readonly TransportPath[] { return [this]; }
  run(script: string, input: unknown, timeoutMs: number): Promise<string> {
    this.signal.throwIfAborted();
    return this.exec([this.python, "-c", script], { stdin: JSON.stringify(input), timeoutMs });
  }
}

export type TransportPath = TcpTransport | PodPythonTransport;

/** Only failure to establish a connection permits a route change; never replay protocol operations. */
export function isConnectionNetworkError(error: unknown): boolean {
  if (!error || typeof error !== "object" || !("code" in error)) return false;
  return ["ECONNREFUSED", "ETIMEDOUT", "ECONNRESET", "EHOSTUNREACH", "ENETUNREACH",
    "EADDRNOTAVAIL", "ENOTFOUND", "EAI_AGAIN"].includes(String(error.code));
}

export { PodRelayTransport, type PodRelayOptions } from "./pod-relay";
export { ServicePortForwardTransport } from "./service-port-forward";
