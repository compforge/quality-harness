import type { Client } from "./client.js";
import type { ClientProvider } from "./client-provider.js";
import type { Service } from "../system/service.js";

/**
 * A source of data; accessible environments implement ClientProvider directly.
 * @spec kind is a nonempty, open backend identifier for consumer-owned query selection.
 */
export interface DataSource<C extends Client> extends ClientProvider<C> {
  /** Backend implementation; not a configured source name or query-language dialect. */
  readonly kind: string;
  /** Safe descriptive metadata for discovery; never credentials or part of clientKey. */
  readonly description?: string;
}

/**
 * @spec A Service association does not own a client or change the source identity.
 * @why Business ownership must not fragment reuse of the same source's client.
 */
export interface ServiceDataSource<S extends Service, C extends Client> {
  readonly service: S;
  readonly source: DataSource<C>;
}
