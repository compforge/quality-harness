export { clientKey, ClientManager, EnvironmentContext, type Client, type ClientProvider, type DataSource, type ServiceDataSource } from "@compforge/harness-common";
export { ToolboxError, KubernetesError, type ErrorKind } from "./errors";

export interface ClientLifecycle {
  signal?: AbortSignal;
  onDispose?: (dispose: () => Promise<void>) => void;
  /** Safe route facts only: no credentials, SQL or protocol payloads. */
  onRoute?: (route: { transport: string; reason?: string }) => void;
}
