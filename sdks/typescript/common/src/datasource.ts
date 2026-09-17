import type { Client } from "./client.js";
import type { ClientProvider } from "./client-provider.js";
import type { Service } from "./service.js";

/** A source of data; accessible environments implement ClientProvider directly. */
export interface DataSource<C extends Client> extends ClientProvider<C> {}

/**
 * @spec A Service association does not own a client or change the source identity.
 * @why Business ownership must not fragment reuse of the same source's client.
 */
export interface ServiceDataSource<S extends Service, C extends Client> {
  readonly service: S;
  readonly source: DataSource<C>;
}
