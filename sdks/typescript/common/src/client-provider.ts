import { createHash } from "node:crypto";
import type { Client } from "./client.js";
import type { ClientManager } from "./client-manager.js";

/**
 * Keyed construction independent of data or environment semantics.
 * @spec Both environment access and data access share ClientManager ownership.
 * @rule Identity covers implementation, target, credentials and capacity policy.
 */
export interface ClientProvider<C extends Client> {
  readonly clientKey: string;
  /** Construct only; initialize borrows dependencies through clients before publishing readiness.
   * Dependencies must be acyclic; borrowers never dispose them. No environment model is required.
   */
  createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): C;
}

/** Stable configuration identity; credentials never appear in observable keys. */
export function clientKey(protocol: string, configuration: unknown): string {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]),
    );
    return value;
  };
  return `${protocol}:${createHash("sha256").update(JSON.stringify(canonical(configuration))).digest("hex")}`;
}
