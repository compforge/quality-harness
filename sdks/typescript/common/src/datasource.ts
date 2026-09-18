import type { Client } from "./client.js";
import type { JsonObject } from "./json.js";
import type { ClientProvider } from "./client-provider.js";
import type { Service } from "./service.js";

/** A data-access client that can describe the target it actually resolved. */
export interface DataSourceClient extends Client {
  /**
   * @spec Return a fresh, masked target representation after initialization; perform no I/O and mutate no source state.
   * @rule Select safe fields explicitly; omit credentials, credential-bearing URL parts and raw configuration.
   * This describes the target, not connection health or query success.
   */
  mask(): JsonObject;
}

/** A source of data; accessible environments implement ClientProvider directly. */
export interface DataSource<C extends Client> extends ClientProvider<C> {
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
