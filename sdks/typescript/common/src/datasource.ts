import { createHash } from "node:crypto";
import type { Client } from "./client.js";

/** Identity includes protocol, target, configuration and credentials, never an object address. */
export interface DataSource<C extends Client> {
  readonly key: string;
  /** Construct only; Client.initialize owns external work. */
  createClient(signal: AbortSignal): C;
}

/**
 * @spec A Service association does not own a client or change the source identity.
 * @why Service models belong to consumers; binding the same source must not fragment client reuse.
 */
export interface ServiceDataSource<Service, C extends Client> {
  readonly service: Service;
  readonly source: DataSource<C>;
}

/** Stable in-memory identity; connection credentials must not appear in observable cache keys. */
export function dataSourceKey(protocol: string, configuration: unknown): string {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]),
    );
    return value;
  };
  return `${protocol}:${createHash("sha256").update(JSON.stringify(canonical(configuration))).digest("hex")}`;
}
