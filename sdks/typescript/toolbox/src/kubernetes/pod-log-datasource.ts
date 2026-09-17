import { clientKey, type DataSource, type ClientManager } from "../datasource";
import type { ConcurrencyPool } from "../concurrency";
import { PodLogClient } from "./pod-log-client";
import type { PodLogByteBudget, PodLogCapturePolicy } from "./log-capture-plan";

/** Target identity is separate from each Pod/container snapshot identity. Limits belong to the root caller. */
export class PodLogDataSource implements DataSource<PodLogClient> {
  readonly clientKey: string;
  constructor(
    target: { kubeconfig?: string; context?: string; namespace: string },
    private readonly pool: ConcurrencyPool,
    private readonly budget: PodLogByteBudget,
    private readonly policy: PodLogCapturePolicy,
  ) { this.clientKey = clientKey("pod-log", target); }
  createClient(_clients: Pick<ClientManager, "get">, signal: AbortSignal): PodLogClient {
    return new PodLogClient(this.pool, signal, this.policy, this.budget);
  }
}
