import type { Client } from "./client.js";
import type { ClientProvider } from "./client-provider.js";

/** A source of data; accessible environments implement ClientProvider directly. */
export interface DataSource<C extends Client> extends ClientProvider<C> {}

/**
 * @spec A Service association does not own a client or change the source identity.
 * @why Service models belong to consumers; binding the same source must not fragment client reuse.
 */
export interface ServiceDataSource<Service, C extends Client> {
  readonly service: Service;
  readonly source: DataSource<C>;
}
