import { Transport } from "./lifecycle";
import type { Endpoint, TcpTransport, TransportPath } from "./index";
import { raceSignal } from "race-signal";
import type { ConnectionAttempt } from "../connection/acquire";
import { KubectlExecutor, type Executor, type KubectlOptions } from "../kubernetes/executor";
import { ServicePortForwarder } from "../kubernetes/service-port-forward";
import type { StartPortForward } from "../kubernetes/port-forward";

/**
 * @spec One manager-owned transport shares forwards across protocol clients in the same namespace.
 * @rule Cancelling a borrower does not stop a shared forward; root disposal releases it.
 * @see {@link component://docs/toolbox.md}
 */
export class ServicePortForwardTransport extends Transport implements TcpTransport {
  readonly kind = "tcp";
  readonly name = "port-forward";
  #initialization?: Promise<void>;
  #forwarder?: ServicePortForwarder;

  constructor(private readonly options: KubectlOptions & { namespace: string; signal?: AbortSignal;
    onDiagnostic?: (line: string) => void }, private readonly dependencies?: { executor?: Executor; startForward?: StartPortForward }) {
    super([], options.signal);
  }

  protected override paths(): readonly TransportPath[] { return [this]; }
  override initialize(): Promise<void> {
    this.signal.throwIfAborted();
    const pending = this.#initialization ??= (async () => {
      this.#forwarder = await ServicePortForwarder.create(this.dependencies?.executor ?? new KubectlExecutor(this.options),
        { ...this.options, signal: this.resourceSignal }, this.dependencies?.startForward);
      this.signal.throwIfAborted();
    })();
    return raceSignal(pending, this.signal);
  }

  get activeForwards() { return this.#forwarder?.activeForwards ?? []; }

  async connect(endpoint: Endpoint, attempt?: ConnectionAttempt): Promise<Endpoint> {
    this.signal.throwIfAborted();
    const signal = attempt ? AbortSignal.any([this.signal, attempt.signal]) : this.signal;
    await raceSignal(this.initialize(), signal);
    const mapped = await raceSignal(this.#forwarder!.forward(endpoint), signal);
    return { host: mapped.host, port: mapped.port };
  }

  protected override async close(): Promise<void> {
    await this.#initialization?.catch(() => {});
    await this.#forwarder?.dispose();
  }
}
