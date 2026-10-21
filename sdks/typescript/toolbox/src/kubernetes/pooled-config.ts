import { KubeConfig } from "@kubernetes/client-node";
import type { Agent } from "node:http";
import type { RequestOptions } from "node:https";
import { clientKey } from "../client";

/**
 * @spec Refresh credentials per request while reusing Agents only for identical proxy/TLS settings.
 * @why client-node otherwise creates a new Agent on every applyToHTTPSOptions call.
 * @see {@link docs/toolbox.md}
 */
export class PooledKubeConfig extends KubeConfig {
  readonly #agents = new Map<string, Agent>();
  #disposed = false;

  constructor(private readonly maxSockets: number) { super(); }

  override async applyToHTTPSOptions(options: RequestOptions): Promise<void> {
    if (this.#disposed) throw new Error("Kubernetes HTTP pool disposed");
    await super.applyToHTTPSOptions(options);
    // The SDK selects the correct HTTP, HTTPS or proxy Agent and supplies its TLS options.
    const fresh = options.agent as Agent & { keepAlive: boolean };
    if (this.#disposed) { fresh.destroy(); throw new Error("Kubernetes HTTP pool disposed"); }
    const cluster = this.getCurrentCluster();
    const tls = { ...options, agent: undefined, headers: undefined, auth: undefined };
    const key = clientKey("kubernetes-agent", { server: cluster?.server, proxy: cluster?.proxyUrl, tls });
    const shared = this.#agents.get(key);
    if (shared) {
      fresh.destroy();
      options.agent = shared;
      return;
    }
    fresh.keepAlive = true;
    fresh.maxSockets = this.maxSockets;
    fresh.maxTotalSockets = this.maxSockets;
    fresh.maxFreeSockets = this.maxSockets;
    this.#agents.set(key, fresh);
  }

  dispose(): void {
    this.#disposed = true;
    for (const agent of this.#agents.values()) agent.destroy();
    this.#agents.clear();
  }
}
