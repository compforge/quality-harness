import { KubernetesExecTransport } from "./exec-transport";
import type { Client } from "../client";
import { KubectlExecutor, type Executor, type ExecTarget, type RunOptions } from "./executor";
import { ServicePortForwarder } from "./service/port-forward";
import type { KubernetesEndpoint } from "./endpoint";
import type { Workload, WorkloadInstance } from "@compforge/harness-common";
import type { SyncedCollection } from "./resource-cache";
import { resourcePath } from "./resources";
import type { ResourceCacheQuery, ResourceAccess, ResourceLimits, ResourceRef, ResourceListQuery, ResourceRequestOptions, NamespaceResources } from "./resources";
import { KubernetesResourceTransport, type KubernetesConnectionOptions } from "./resource-transport";
import { resolveWorkload } from "./workload";
import { KubernetesError } from "../errors";

export interface KubernetesClientOptions extends KubernetesConnectionOptions {
  namespace: string;
  /** Keep complete list queries synchronized via WATCH. Defaults to direct API reads. */
  useCache?: boolean;
}

/** Owns cluster transports; protocol callers borrow this client without closing it. */
export class KubernetesClient implements Client {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  readonly #executors = new Map<string, Executor>();
  readonly #forwarders = new Map<string, Promise<ServicePortForwarder>>();
  readonly #operations = new Set<Promise<unknown>>();
  readonly #caches = new Map<string, SyncedCollection>();
  #disposal?: Promise<void>;
  #exec?: KubernetesExecTransport;
  readonly #injectedExecutor?: Executor;
  #resources?: ResourceAccess;
  #transport?: KubernetesResourceTransport;

  constructor(private readonly kube: KubernetesClientOptions, signal?: AbortSignal, executor?: Executor,
    private readonly resourceLimits: ResourceLimits = { timeoutMs: 30_000, concurrency: 4, maxBytes: 8 * 1024 * 1024 },
    resources?: ResourceAccess) {
    this.#injectedExecutor = executor;
    this.signal = signal ? AbortSignal.any([signal, this.#controller.signal]) : this.#controller.signal;
    if (executor) this.#executors.set(kube.namespace, executor);
    this.#resources = resources;
  }

  async initialize(): Promise<void> { this.signal.throwIfAborted(); }

  /** environment is a stable target ID from the caller's registry, not access configuration. */
  resolveWorkload(workload: Workload, environment: string): Promise<WorkloadInstance[]> {
    return this.#track(() => {
      const namespace = workload.namespace ?? this.kube.namespace;
      if (!namespace.trim()) throw new KubernetesError("Workload namespace is required", { kind: "invalid_argument" });
      if (!environment.trim()) throw new KubernetesError("Environment identity is required", { kind: "invalid_argument" });
      return resolveWorkload(this, workload, namespace, environment);
    });
  }

  #resourceAccess(): ResourceAccess {
    return this.#resources ??= this.#transport = new KubernetesResourceTransport(this.kube, this.signal, this.resourceLimits);
  }

  #executor(namespace: string): Executor {
    let executor = this.#executors.get(namespace);
    if (!executor) { executor = new KubectlExecutor({ ...this.kube, namespace }); this.#executors.set(namespace, executor); }
    return executor;
  }

  #track<T>(work: () => T | Promise<T>): Promise<T> {
    this.signal.throwIfAborted();
    const pending = Promise.resolve().then(() => { this.signal.throwIfAborted(); return work(); });
    this.#operations.add(pending);
    void pending.then(() => this.#operations.delete(pending), () => this.#operations.delete(pending));
    return pending;
  }

  #options(options?: RunOptions): RunOptions {
    return { ...options, signal: options?.signal ? AbortSignal.any([this.signal, options.signal]) : this.signal };
  }

  /**
   * @spec Resource operations return API objects and structured errors, sharing one root HTTP transport.
   * @rule Namespace views borrow the root lifecycle; reads never fall back to command execution.
   * @see {@link docs/toolbox.md}
   */
  get(ref: ResourceRef, options?: ResourceRequestOptions) {
    return this.#track(() => this.#resourceAccess().get(
      { ...ref, namespace: ref.namespace ?? this.kube.namespace }, this.#options(options)));
  }

  list(query: ResourceListQuery, options?: ResourceRequestOptions) {
    return this.#list({ ...query, namespace: query.namespace ?? this.kube.namespace }, options);
  }

  #list(query: ResourceListQuery, options?: ResourceRequestOptions) {
    return this.#track(() => {
      const request = this.#options(options);
      request.signal?.throwIfAborted();
      const access = this.#resourceAccess();
      if (!this.kube.useCache || !this.#transport || query.limit !== undefined || query.continue !== undefined) {
        return access.list(query, request);
      }
      if (options?.timeoutMs !== undefined && (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0)) {
        throw new KubernetesError("Resource timeout must be a positive integer", { kind: "invalid_argument" });
      }
      const key = collectionKey(query);
      const cache = this.#caches.get(key);
      if (cache) return cache.snapshot() ?? access.list(query, request);

      // Give the foreground read the first transport slot on a cold collection.
      const result = access.list(query, request);
      this.#caches.set(key, this.#transport.cache({ ...query }));
      return result;
    });
  }

  /** Explicit root scope for Nodes and all-namespace lists; no default namespace is inserted.
   * @spec This view borrows root transport, cancellation and cleanup just like namespace views.
   */
  clusterResources(): ResourceAccess {
    return {
      get: (ref, options) => this.#track(() => this.#resourceAccess().get(ref, this.#options(options))),
      list: (query, options) => this.#list(query, options),
    };
  }

  inNamespace(namespace: string): NamespaceResources {
    if (!namespace.trim()) throw new KubernetesError("Namespace is required", { kind: "invalid_argument" });
    return {
      get: (ref, options) => this.get({ ...ref, namespace }, options),
      list: (query, options) => this.list({ ...query, namespace }, options),
    };
  }
  exec(namespace: string, target: ExecTarget, command: string[], options?: RunOptions) {
    return this.#track(() => this.#injectedExecutor && namespace === this.kube.namespace
      ? this.#injectedExecutor.exec(target, command, this.#options(options))
      : (this.#exec ??= new KubernetesExecTransport(this.kube, this.signal, this.resourceLimits))
        .exec(namespace, target, command, this.#options(options)));
  }
  forward(namespace: string, target: KubernetesEndpoint) {
    return this.#track(async () => {
      let pending = this.#forwarders.get(namespace);
      if (!pending) {
        pending = ServicePortForwarder.create(this.#executor(namespace), { ...this.kube, namespace });
        this.#forwarders.set(namespace, pending);
        void pending.catch(() => { if (this.#forwarders.get(namespace) === pending) this.#forwarders.delete(namespace); });
      }
      const forwarder = await pending;
      this.signal.throwIfAborted();
      return forwarder.forward(target);
    });
  }
  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("Kubernetes client disposed"));
      await Promise.allSettled([...this.#operations, ...Array.from(this.#caches.values(), cache => cache.dispose())]);
      this.#caches.clear();
      const results = await Promise.allSettled([...this.#forwarders.values()].map(async pending => {
        const forwarder = await pending.catch(() => undefined);
        forwarder?.stop();
      }));
      this.#forwarders.clear();
      this.#transport?.dispose();
      const errors = results.flatMap(result => result.status === "rejected" ? [result.reason] : []);
      if (errors.length) throw new AggregateError(errors, "Kubernetes client cleanup failed");
    })();
  }
}

function collectionKey(query: ResourceCacheQuery): string {
  return JSON.stringify([resourcePath(query), query.labelSelector || "", query.fieldSelector || ""]);
}
