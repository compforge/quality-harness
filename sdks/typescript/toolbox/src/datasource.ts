export { clientKey, ClientManager, EnvironmentContext, type Client, type ClientProvider, type DataSource, type ServiceDataSource } from "@compforge/harness-common";
import type { Transport } from "./transport";
export { ToolboxError, KubernetesError, type ErrorKind } from "./errors";

/** Resolution owns configuration semantics; transports preserve the resolved target and identity. */
export interface ConnectionSource<Target> {
  resolve(): Promise<Target>;
  readonly transports: readonly Transport[];
}

export interface ClientLifecycle {
  signal?: AbortSignal;
  onDispose?: (dispose: () => Promise<void>) => void;
  /** Safe route facts only: no credentials, SQL or protocol payloads. */
  onRoute?: (route: { transport: string; reason?: string }) => void;
}
