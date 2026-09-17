import { clientKey, type ClientProvider, type ClientManager } from "../client";
import { KubernetesClient } from "./client";
import type { KubectlOptions } from "./executor";
import type { ResourceLimits } from "./resources";

/** Cluster access is a client provider; the execution owns client lifetime. */
export class KubernetesEnvironment implements ClientProvider<KubernetesClient> {
  readonly clientKey: string;
  /** Deployment metadata only; not part of client reuse or access configuration. */
  readonly imageRegistry?: string;
  readonly #kube: KubectlOptions & { namespace: string };
  readonly #limits: ResourceLimits;

  constructor(readonly name: string, kube: KubectlOptions & { namespace: string; imageRegistry?: string }, limits: ResourceLimits) {
    this.imageRegistry = kube.imageRegistry;
    // A structural environment object may carry names/IDs; only access fields key reuse.
    this.#kube = Object.freeze({
      namespace: kube.namespace, kubeconfig: kube.kubeconfig, context: kube.context,
    });
    this.#limits = Object.freeze({ ...limits });
    this.clientKey = clientKey("kubernetes-client", [this.#kube, this.#limits]);
  }

  createClient(_clients: Pick<ClientManager, "get">, signal: AbortSignal): KubernetesClient {
    return new KubernetesClient(this.#kube, signal, undefined, this.#limits);
  }
}
