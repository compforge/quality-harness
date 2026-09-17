import type { Client } from "../client";
import { KubectlExecutor, type Executor, type KubectlOptions, type ExecTarget, type RunOptions } from "./executor";
import { ServicePortForwarder } from "./service-port-forward";
import type { KubernetesEndpoint } from "./endpoint";
import type { Workload, WorkloadInstance } from "@compforge/harness-common";
import { resourceAccess, type ResourceAccess, type ResourceLimits } from "./resources";
import { resolveWorkload } from "./workload";
import { KubernetesError } from "../errors";

/** Owns cluster transports; protocol callers borrow this client without closing it. */
export class KubernetesClient implements Client {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  readonly #executors = new Map<string, Executor>();
  readonly #forwarders = new Map<string, Promise<ServicePortForwarder>>();
  readonly #operations = new Set<Promise<unknown>>();
  #disposal?: Promise<void>;
  #resources?: ResourceAccess;

  constructor(private readonly kube: KubectlOptions & { namespace: string }, signal?: AbortSignal, executor?: Executor,
    private readonly resourceLimits: ResourceLimits = { timeoutMs: 30_000, concurrency: 4, maxBytes: 8 * 1024 * 1024 },
    resources?: ResourceAccess) {
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
      this.#resources ??= resourceAccess(this.kube, this.signal, this.resourceLimits);
      return resolveWorkload(this.#resources, workload, namespace, environment);
    });
  }

  #executor(namespace: string): Executor {
    let executor = this.#executors.get(namespace);
    if (!executor) { executor = new KubectlExecutor({ ...this.kube, namespace }); this.#executors.set(namespace, executor); }
    return executor;
  }

  #track<T>(work: () => Promise<T>): Promise<T> {
    this.signal.throwIfAborted();
    const pending = Promise.resolve().then(() => { this.signal.throwIfAborted(); return work(); });
    this.#operations.add(pending);
    void pending.then(() => this.#operations.delete(pending), () => this.#operations.delete(pending));
    return pending;
  }

  #options(options?: RunOptions): RunOptions {
    return { ...options, signal: options?.signal ? AbortSignal.any([this.signal, options.signal]) : this.signal };
  }

  run(namespace: string, command: string[], options?: RunOptions) {
    return this.#track(() => this.#executor(namespace).run(command, this.#options(options)));
  }
  exec(namespace: string, target: ExecTarget, command: string[], options?: RunOptions) {
    return this.#track(() => this.#executor(namespace).exec(target, command, this.#options(options)));
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
      await Promise.allSettled(this.#operations);
      const results = await Promise.allSettled([...this.#forwarders.values()].map(async pending => {
        const forwarder = await pending.catch(() => undefined);
        forwarder?.stop();
      }));
      this.#forwarders.clear();
      const errors = results.flatMap(result => result.status === "rejected" ? [result.reason] : []);
      if (errors.length) throw new AggregateError(errors, "Kubernetes client cleanup failed");
    })();
  }
}
