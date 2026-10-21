import { posix } from "node:path";
import { clientKey, FileDataSource, type ClientManager, type ClientProvider, type FileReadOptions } from "@compforge/harness-common";
import type { KubernetesClient } from "../kubernetes/client";
import { FileError } from "../errors";
import { BoundedFileClient, fileClientOptions, type FileClientOptions } from "./client";

export interface PodFileTarget { namespace: string; pod: string; container: string }
export type PodFileAccess = Pick<KubernetesClient, "initialize" | "dispose" | "exec">;

export class PodFileClient extends BoundedFileClient {
  #kubernetes?: PodFileAccess;
  constructor(path: string, readonly target: Readonly<PodFileTarget>, options: FileClientOptions, signal: AbortSignal,
    private readonly acquire: () => Promise<PodFileAccess>) {
    super(path, options, signal);
  }
  protected async initializeAccess(): Promise<void> { this.#kubernetes = await this.acquire(); }
  protected async capture(signal: AbortSignal, limits: Pick<FileReadOptions, "maxBytes" | "timeoutMs">, receive: (bytes: Uint8Array) => void): Promise<void> {
    // Read one extra byte to distinguish an exact-size complete file from an oversized one.
    // argv stays separate; the absolute path is data even when it contains shell metacharacters.
    const result = await this.#kubernetes!.exec(this.target.namespace, this.target,
      ["head", "-c", String(limits.maxBytes + 1), this.path], { signal, timeoutMs: limits.timeoutMs, collectStdout: false, onStdoutBytes: receive });
    signal.throwIfAborted();
    if (!result.ok) throw new FileError(
      `Cannot read Pod file: ${this.target.namespace}/${this.target.pod}/${this.target.container} ${this.path}`,
      { kind: result.timedOut ? "timeout" : "operation_failed", code: result.exitCode ?? undefined });
  }
}

/**
 * @spec One explicit Pod/container/path per source; workload discovery and replica traversal belong to callers.
 * @rule Borrow Kubernetes access from the root manager; file clients never dispose that shared dependency.
 * @see {@link component://../../../docs/toolbox.md}
 */
export class PodFileDataSource extends FileDataSource<PodFileClient> {
  readonly path: string;
  readonly target: Readonly<PodFileTarget>;
  readonly clientKey: string;
  readonly #options: Readonly<FileClientOptions>;

  constructor(private readonly kubernetes: ClientProvider<PodFileAccess>, target: PodFileTarget,
    path: string, options: FileClientOptions) {
    super();
    if (!posix.isAbsolute(path) || path.includes("\0")) {
      throw new FileError("Pod file path must be absolute and contain no NUL", { kind: "invalid_argument" });
    }
    if ([target.namespace, target.pod, target.container].some(value => !value.trim())) {
      throw new FileError("Pod file namespace, Pod and Container are required", { kind: "invalid_argument" });
    }
    this.path = path;
    this.target = Object.freeze({ namespace: target.namespace, pod: target.pod, container: target.container });
    this.#options = fileClientOptions(options);
    this.clientKey = clientKey("pod-file", { access: kubernetes.clientKey, target: this.target,
      path: this.path, options: this.#options });
  }
  createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): PodFileClient {
    return new PodFileClient(this.path, this.target, this.#options, signal, () => clients.get(this.kubernetes));
  }
}
