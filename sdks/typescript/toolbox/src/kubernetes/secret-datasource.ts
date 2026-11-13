import { clientKey, type Client, type ClientManager, type ClientProvider, type DataSource } from "@compforge/harness-common";
import { KubernetesError } from "../errors";
import { resourcePath, type Resource, type ResourceAccess, type ResourceRequestOptions } from "./resources";

export interface KubernetesSecretTarget {
  readonly namespace: string;
  readonly name: string;
}

export interface KubernetesSecretRead {
  resourceVersion?: string;
  observedAt: string;
  values: Record<string, string>;
  /** A successful read can establish absence without treating it as an API failure. */
  missingKeys: string[];
}

export type KubernetesSecretAccess = Client & Pick<ResourceAccess, "get">;

export function secretKeys(keys: readonly string[]): readonly string[] {
  if (!keys.length || keys.some(key => !/^[A-Za-z0-9._-]+$/.test(key)) || new Set(keys).size !== keys.length) {
    throw new KubernetesError("Secret keys must be nonempty, distinct Kubernetes data keys", { kind: "invalid_argument" });
  }
  return Object.freeze([...keys]);
}

function secretTarget(target: KubernetesSecretTarget): Readonly<KubernetesSecretTarget> {
  if (!target.namespace || !target.name) throw new KubernetesError("Secret target requires namespace and name", { kind: "invalid_argument" });
  resourcePath({ apiVersion: "v1", resource: "secrets", namespace: target.namespace }, target.name);
  return Object.freeze({ namespace: target.namespace, name: target.name });
}

/**
 * @spec Only declared UTF-8 keys enter results; Kubernetes failures retain their kind and never become missing keys.
 * @rule The borrowed resource access owns transport capacity and byte limits; request deadlines and cancellation flow through get.
 * @see {@link component://../../../docs/toolbox.md}
 */
export class KubernetesSecretClient implements Client {
  readonly keys: readonly string[];
  readonly #controller = new AbortController();
  readonly #signal: AbortSignal;
  readonly #operations = new Set<Promise<KubernetesSecretRead>>();
  #target?: Readonly<KubernetesSecretTarget>;
  #access?: Pick<ResourceAccess, "get">;
  #disposal?: Promise<void>;

  constructor(keys: readonly string[], signal: AbortSignal, private readonly acquire: () => Promise<{
    target: KubernetesSecretTarget; access: Pick<ResourceAccess, "get">;
  }>) {
    this.keys = secretKeys(keys);
    this.#signal = AbortSignal.any([signal, this.#controller.signal]);
  }

  get target(): Readonly<KubernetesSecretTarget> {
    if (!this.#target) throw new KubernetesError("Secret client is not initialized", { kind: "operation_failed" });
    return this.#target;
  }

  async initialize(): Promise<void> {
    this.#signal.throwIfAborted();
    const { target, access } = await this.acquire();
    this.#signal.throwIfAborted();
    this.#target = secretTarget(target);
    this.#access = access;
  }

  async read(options: ResourceRequestOptions = {}): Promise<KubernetesSecretRead> {
    const signal = options.signal ? AbortSignal.any([this.#signal, options.signal]) : this.#signal;
    signal.throwIfAborted();
    const target = this.target;
    const operation = this.capture(target, { ...options, signal });
    this.#operations.add(operation);
    try { return await operation; }
    finally { this.#operations.delete(operation); }
  }

  private async capture(target: KubernetesSecretTarget, options: ResourceRequestOptions): Promise<KubernetesSecretRead> {
    const secret = await this.#access!.get({ apiVersion: "v1", resource: "secrets", ...target }, options) as Resource & {
      data?: Record<string, string>;
    };
    options.signal!.throwIfAborted();
    const values: Record<string, string> = Object.create(null);
    const missingKeys: string[] = [];
    for (const key of this.keys) {
      if (!Object.hasOwn(secret.data ?? {}, key)) { missingKeys.push(key); continue; }
      const encoded = secret.data![key]!;
      try {
        const bytes = Buffer.from(encoded, "base64");
        if (bytes.toString("base64") !== encoded) throw new Error("invalid base64");
        values[key] = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        // Native decode errors can include credential-bearing input. Do not retain them as causes.
        throw new KubernetesError(`Secret key is not valid base64 UTF-8: ${key}`, { kind: "invalid_response" });
      }
    }
    return { values, missingKeys, observedAt: new Date().toISOString(),
      ...(typeof secret.metadata?.resourceVersion === "string" ? { resourceVersion: secret.metadata.resourceVersion } : {}) };
  }

  /** @spec Disposal cancels and drains owned reads without closing borrowed Kubernetes access. */
  dispose(): Promise<void> {
    this.#disposal ??= (async () => {
      this.#controller.abort(new KubernetesError("Secret client disposed", { kind: "operation_failed" }));
      await Promise.allSettled([...this.#operations]);
      this.#target = undefined;
      this.#access = undefined;
    })();
    return this.#disposal;
  }
}

/** Named Secret acquisition; callers own discovery, access policy and interpretation of the selected values. */
export class KubernetesSecretDataSource implements DataSource<KubernetesSecretClient> {
  readonly kind = "kubernetes_secret";
  readonly clientKey: string;
  readonly target: Readonly<KubernetesSecretTarget>;
  readonly keys: readonly string[];

  constructor(private readonly kubernetes: ClientProvider<KubernetesSecretAccess>, target: KubernetesSecretTarget, keys: readonly string[]) {
    this.target = secretTarget(target);
    this.keys = secretKeys(keys);
    this.clientKey = clientKey("kubernetes-secret", { access: kubernetes.clientKey, target: this.target, keys: this.keys });
  }

  createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): KubernetesSecretClient {
    return new KubernetesSecretClient(this.keys, signal, async () => ({ target: this.target, access: await clients.get(this.kubernetes) }));
  }
}
