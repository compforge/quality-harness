import type { Host } from "./host.js";

/**
 * Named deployment environment, independent of any access mechanism.
 * @rule Accessible environments additionally implement ClientProvider; identity alone grants no access.
 */
export interface Environment {
  readonly name: string;
  /** Omission denotes a generic environment. Concrete platforms declare their kind. */
  readonly kind?: string;
  /** Omission uses the current execution host, without changing environment identity. */
  readonly host?: Host;
}

/** Target processes run directly on the optional host, locally or through SSH. */
export interface HostEnvironment extends Environment {
  readonly kind: "host";
}
