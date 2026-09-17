import { clientKey, type ClientFactory, type ClientProvider } from "../client";
import { KubernetesClient } from "./client";
import type { KubectlOptions } from "./executor";
import type { ResourceLimits } from "./resources";

/** Environment access factory, not a data source or a separate client owner. */
export class KubernetesClientFactory implements ClientFactory<KubernetesClient> {
  readonly key: string;
  readonly #kube: KubectlOptions & { namespace: string };
  readonly #limits: ResourceLimits;

  constructor(kube: KubectlOptions & { namespace: string }, limits: ResourceLimits) {
    // A structural environment object may carry names/IDs; only access fields key reuse.
    this.#kube = Object.freeze({
      namespace: kube.namespace, kubeconfig: kube.kubeconfig, context: kube.context,
    });
    this.#limits = Object.freeze({ ...limits });
    this.key = clientKey("kubernetes-client", [this.#kube, this.#limits]);
  }

  createClient(_clients: ClientProvider, signal: AbortSignal): KubernetesClient {
    return new KubernetesClient(this.#kube, signal, undefined, this.#limits);
  }
}
