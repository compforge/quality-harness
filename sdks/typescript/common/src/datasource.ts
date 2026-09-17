import type { Client } from "./client.js";
import type { ClientFactory } from "./client-factory.js";

/** A source of data; environment management factories need only ClientFactory. */
export interface DataSource<C extends Client> extends ClientFactory<C> {}

/**
 * @spec A Service association does not own a client or change the source identity.
 * @why Service models belong to consumers; binding the same source must not fragment client reuse.
 */
export interface ServiceDataSource<Service, C extends Client> {
  readonly service: Service;
  readonly source: DataSource<C>;
}
