import { posix } from "node:path";
import { clientKey, FileDataSource, type ClientManager, type ClientProvider, type FileReadOptions } from "@compforge/harness-common";
import type { KubernetesClient } from "../kubernetes/client";
import { FileError } from "../errors";
import { BoundedFileClient, fileClientOptions, filePaths, type FileClientOptions, type FileCaptureSink } from "./client";

export interface PodFileTarget { namespace: string; pod: string; container: string }
export type PodFileAccess = Pick<KubernetesClient, "initialize" | "dispose" | "exec">;

// One indexed base64 record per file, including a fixed-width head exit status suffix.
// Paths are positional arguments, never shell source. No temporary files or remote writes are needed.
const READ_FILES = String.raw`limit=$1
shift
index=0
for path do
  printf '%s ' "$index"
  { if [ -f "$path" ]; then head -c "$limit" "$path" 2>/dev/null; status=$?; else status=1; fi
    printf '\000%03d' "$status"
  } | base64 | tr -d '\r\n'
  printf '\n'
  index=$((index + 1))
done`;

/** Decode complete base64 quartets immediately, retaining only the four-byte per-file status trailer. */
function fileRecords(paths: readonly string[], maxBytes: number, target: PodFileTarget, sink: FileCaptureSink) {
  let index = 0, header = "", started = false, carry = "", encodedSize = 0, padded = false;
  let trailer: Buffer = Buffer.alloc(0);
  const maximumEncoded = Math.ceil((maxBytes + 5) / 3) * 4;
  const invalid = () => new FileError("Invalid Pod file group response", { kind: "invalid_response" });
  const body = (part: string) => {
    if (padded && part) throw invalid();
    encodedSize += part.length;
    if (encodedSize > maximumEncoded) throw invalid();
    const text = carry + part, end = text.length - text.length % 4;
    carry = text.slice(end);
    if (!end) return;
    const encoded = text.slice(0, end), decoded = Buffer.from(encoded, "base64");
    if (decoded.toString("base64") !== encoded) throw invalid();
    padded = encoded.endsWith("=");
    if (padded && carry) throw invalid();
    const bytes = Buffer.concat([trailer, decoded]);
    if (bytes.length > 4) sink.receive(index, bytes.subarray(0, -4));
    trailer = Buffer.from(bytes.subarray(Math.max(0, bytes.length - 4)));
  };
  return {
    receive(bytes: Uint8Array) {
      if (bytes.some(byte => byte > 127)) throw invalid();
      const text = Buffer.from(bytes).toString("ascii");
      for (let offset = 0; offset < text.length;) {
        if (!started) {
          const char = text[offset++]!;
          if (char !== " ") {
            if (!/\d/.test(char) || header.length >= String(paths.length).length) throw invalid();
            header += char;
            continue;
          }
          if (index >= paths.length || header !== String(index)) throw invalid();
          started = true;
        }
        const newline = text.indexOf("\n", offset);
        body(text.slice(offset, newline < 0 ? text.length : newline));
        if (newline < 0) return;
        if (carry || trailer.length !== 4 || trailer[0] !== 0) throw invalid();
        const status = trailer.subarray(1).toString("ascii");
        if (!/^\d{3}$/.test(status) || Number(status) > 255) throw invalid();
        sink.complete(index, Number(status) === 0 ? undefined : new FileError(
          `Cannot read Pod file: ${target.namespace}/${target.pod}/${target.container} ${paths[index]}`,
          { kind: "operation_failed", code: Number(status) }));
        index++; header = ""; started = false; encodedSize = 0; padded = false; trailer = Buffer.alloc(0);
        offset = newline + 1;
      }
    },
    finish() { if (started || header || index !== paths.length) throw invalid(); },
  };
}

export class PodFileClient extends BoundedFileClient {
  #kubernetes?: PodFileAccess;
  constructor(paths: readonly string[], readonly target: Readonly<PodFileTarget>, options: FileClientOptions, signal: AbortSignal,
    private readonly acquire: () => Promise<PodFileAccess>) { super(paths, options, signal); }
  protected async initializeAccess(): Promise<void> { this.#kubernetes = await this.acquire(); }

  /** @spec The whole declared group uses exactly one exec; file failures are records, never implicit retries. */
  protected async capture(signal: AbortSignal, limits: FileReadOptions, sink: FileCaptureSink): Promise<void> {
    const abort = new AbortController();
    const combined = AbortSignal.any([signal, abort.signal]);
    const records = fileRecords(this.paths, limits.maxBytes, this.target, sink);
    const result = await this.#kubernetes!.exec(this.target.namespace, this.target,
      ["sh", "-c", READ_FILES, "file-source", String(limits.maxBytes + 1), ...this.paths], {
        signal: combined, timeoutMs: limits.timeoutMs, collectStdout: false,
        onStdoutBytes: bytes => {
          if (combined.aborted) return;
          try {
            records.receive(bytes);
            combined.throwIfAborted();
          } catch (error) { abort.abort(error); }
        },
      });
    combined.throwIfAborted();
    if (!result.ok) throw new FileError(`Cannot acquire Pod file group: ${this.target.namespace}/${this.target.pod}/${this.target.container}`,
      { kind: result.timedOut ? "timeout" : "operation_failed", code: result.exitCode ?? undefined });
    records.finish();
  }
}

/**
 * @spec One explicit Pod/container and one caller-declared acquisition group; discovery belongs to consumers.
 * @rule Borrow Kubernetes access from the root manager; file clients never dispose that shared dependency.
 * @see {@link component://../../../docs/toolbox.md}
 */
export class PodFileDataSource extends FileDataSource<PodFileClient> {
  readonly paths: readonly string[];
  readonly target: Readonly<PodFileTarget>;
  readonly clientKey: string;
  readonly #options: Readonly<FileClientOptions>;
  constructor(private readonly kubernetes: ClientProvider<PodFileAccess>, target: PodFileTarget,
    paths: readonly string[], options: FileClientOptions) {
    super();
    this.paths = filePaths(paths);
    if (this.paths.some(path => !posix.isAbsolute(path))) throw new FileError("Pod file paths must be absolute", { kind: "invalid_argument" });
    if ([target.namespace, target.pod, target.container].some(value => !value.trim())) {
      throw new FileError("Pod file namespace, Pod and Container are required", { kind: "invalid_argument" });
    }
    this.target = Object.freeze({ namespace: target.namespace, pod: target.pod, container: target.container });
    this.#options = fileClientOptions(options);
    this.clientKey = clientKey("pod-file", { access: kubernetes.clientKey, target: this.target, paths: this.paths, options: this.#options });
  }
  createClient(clients: Pick<ClientManager, "get">, signal: AbortSignal): PodFileClient {
    return new PodFileClient(this.paths, this.target, this.#options, signal, () => clients.get(this.kubernetes));
  }
}
