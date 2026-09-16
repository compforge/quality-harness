import {
  S3Client as AwsS3Client, HeadBucketCommand, HeadObjectCommand, ListBucketsCommand,
  ListObjectsV2Command, GetBucketVersioningCommand, GetObjectCommand,
} from "@aws-sdk/client-s3";
import { Readable } from "node:stream";
import type { Client } from "../client";
import { ConcurrencyPool } from "../concurrency";
import type { ClientLifecycle, ConnectionSource } from "../datasource";
import { s3Handler } from "./handler";
import type { S3Target, S3Limits, S3RequestOptions, S3ObjectMetadata, S3ObjectPage, S3BucketPage, S3ObjectRead } from "./types";

function positive(value: number, name: string, maximum = Number.MAX_SAFE_INTEGER): void {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${name} must be an integer between 1 and ${maximum}`);
  }
}

function metadata(value: { ContentLength?: number; ETag?: string; LastModified?: Date;
  ContentType?: string; VersionId?: string; Metadata?: Record<string, string> }): S3ObjectMetadata {
  return { size: value.ContentLength, etag: value.ETag, lastModified: value.LastModified,
    contentType: value.ContentType, versionId: value.VersionId, metadata: value.Metadata ?? {} };
}

/**
 * @spec Read-only S3 operations share a bounded pool; no failed protocol operation is replayed.
 * @spec HTTP errors retain SDK status/code, including ambiguous HEAD 403/404 responses.
 * @why Initialization prepares a route without requiring account-wide bucket-list permission.
 */
export class S3Client implements Client {
  readonly #controller = new AbortController();
  readonly signal: AbortSignal;
  readonly #pool: ConcurrencyPool;
  readonly #operations = new Set<Promise<unknown>>();
  #initialization?: Promise<void>;
  #disposal?: Promise<void>;
  #sdk?: AwsS3Client;
  #http?: ReturnType<typeof s3Handler>;

  constructor(private readonly source: ConnectionSource<S3Target>, private readonly limits: S3Limits,
    private readonly lifecycle: ClientLifecycle = {}) {
    positive(limits.concurrency, "concurrency");
    positive(limits.connectTimeoutMs, "connectTimeoutMs", 2_147_483_647);
    positive(limits.requestTimeoutMs, "requestTimeoutMs", 2_147_483_647);
    this.#pool = new ConcurrencyPool(limits.concurrency);
    this.signal = lifecycle.signal ? AbortSignal.any([lifecycle.signal, this.#controller.signal]) : this.#controller.signal;
  }

  initialize(): Promise<void> {
    this.signal.throwIfAborted();
    return this.#initialization ??= this.#initialize();
  }

  async #initialize(): Promise<void> {
    const target = await this.source.resolve();
    this.signal.throwIfAborted();
    const url = new URL(target.endpoint);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error("S3 endpoint must be an HTTP(S) URL without embedded credentials, query or fragment");
    }
    const transport = this.source.transports[0];
    if (!transport || transport.kind !== "tcp") throw new Error("S3 requires a TCP transport");
    const route = await transport.connect({ host: url.hostname,
      port: Number(url.port || (url.protocol === "https:" ? 443 : 80)), servername: url.hostname });
    this.signal.throwIfAborted();
    this.#http = s3Handler(target, route, this.limits);
    this.#sdk = new AwsS3Client({ endpoint: target.endpoint, region: target.region,
      credentials: target.credentials, forcePathStyle: target.forcePathStyle ?? true,
      requestHandler: this.#http.handler, maxAttempts: 1, followRegionRedirects: false,
      responseChecksumValidation: "WHEN_REQUIRED" });
    this.lifecycle.onRoute?.({ transport: transport.name });
  }

  #run<T>(options: S3RequestOptions, operation: (sdk: AwsS3Client, signal: AbortSignal) => Promise<T>): Promise<T> {
    this.signal.throwIfAborted();
    const sdk = this.#sdk;
    if (!sdk) throw new Error("S3 client is not initialized");
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(new Error("S3 request deadline exceeded")), this.limits.requestTimeoutMs);
    timer.unref();
    const signal = AbortSignal.any([this.signal, timeout.signal, ...(options.signal ? [options.signal] : [])]);
    const pending = this.#pool.run(() => operation(sdk, signal), signal).finally(() => clearTimeout(timer));
    this.#operations.add(pending);
    void pending.then(() => this.#operations.delete(pending), () => this.#operations.delete(pending));
    return pending;
  }

  /** A failed HEAD remains an error, not a misleading exists=false. */
  headBucket(bucket: string, options: S3RequestOptions = {}): Promise<void> {
    return this.#run(options, async (sdk, signal) => {
      await sdk.send(new HeadBucketCommand({ Bucket: bucket }), { abortSignal: signal });
    });
  }

  headObject(bucket: string, key: string, options: S3RequestOptions & { versionId?: string } = {}): Promise<S3ObjectMetadata> {
    return this.#run(options, async (sdk, signal) => metadata(await sdk.send(
      new HeadObjectCommand({ Bucket: bucket, Key: key, VersionId: options.versionId }), { abortSignal: signal })));
  }

  /** One bounded page; callers own traversal and total scan budgets. */
  listObjects(bucket: string, options: S3RequestOptions & {
    maxKeys: number; prefix?: string; delimiter?: string; continuationToken?: string;
  }): Promise<S3ObjectPage> {
    positive(options.maxKeys, "maxKeys", 1000);
    return this.#run(options, async (sdk, signal) => {
      const page = await sdk.send(new ListObjectsV2Command({ Bucket: bucket, MaxKeys: options.maxKeys,
        Prefix: options.prefix, Delimiter: options.delimiter, ContinuationToken: options.continuationToken }), { abortSignal: signal });
      return { objects: (page.Contents ?? []).filter(item => item.Key !== undefined).map(item => ({
        key: item.Key!, size: item.Size, etag: item.ETag, lastModified: item.LastModified,
      })), prefixes: (page.CommonPrefixes ?? []).flatMap(item => item.Prefix === undefined ? [] : [item.Prefix]),
      truncated: page.IsTruncated ?? false, continuationToken: page.NextContinuationToken };
    });
  }

  listBuckets(options: S3RequestOptions & { maxBuckets: number; continuationToken?: string }): Promise<S3BucketPage> {
    positive(options.maxBuckets, "maxBuckets", 10_000);
    return this.#run(options, async (sdk, signal) => {
      const page = await sdk.send(new ListBucketsCommand({ MaxBuckets: options.maxBuckets,
        ContinuationToken: options.continuationToken }), { abortSignal: signal });
      return { buckets: (page.Buckets ?? []).filter(item => item.Name !== undefined)
        .map(item => ({ name: item.Name!, creationDate: item.CreationDate })), continuationToken: page.ContinuationToken };
    });
  }

  getBucketVersioning(bucket: string, options: S3RequestOptions = {}): Promise<"enabled" | "suspended" | "disabled"> {
    return this.#run(options, async (sdk, signal) => {
      const result = await sdk.send(new GetBucketVersioningCommand({ Bucket: bucket }), { abortSignal: signal });
      if (result.Status === "Enabled") return "enabled";
      return result.Status === "Suspended" ? "suspended" : "disabled";
    });
  }

  /** Read a bounded prefix. Own and close the response stream before returning the pool slot. */
  readObject(bucket: string, key: string, options: S3RequestOptions & {
    maxBytes: number; versionId?: string;
  }): Promise<S3ObjectRead> {
    positive(options.maxBytes, "maxBytes");
    return this.#run(options, async (sdk, signal) => {
      const result = await sdk.send(new GetObjectCommand({ Bucket: bucket, Key: key,
        VersionId: options.versionId }), { abortSignal: signal });
      const body = result.Body;
      if (!(body instanceof Readable)) throw new Error("S3 response is missing a Node readable body");
      const abort = () => body.destroy(new Error("S3 object read aborted", { cause: signal.reason }));
      signal.addEventListener("abort", abort, { once: true });
      const chunks: Buffer[] = [];
      let size = 0;
      let truncated = false;
      try {
        signal.throwIfAborted();
        for await (const chunk of body) {
          signal.throwIfAborted();
          const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          const remaining = options.maxBytes - size;
          chunks.push(Buffer.from(bytes.subarray(0, remaining)));
          size += Math.min(remaining, bytes.length);
          if (bytes.length > remaining || (size === options.maxBytes && result.ContentLength !== undefined && result.ContentLength > size)) {
            truncated = true;
            break;
          }
        }
        return { ...metadata(result), bytes: Buffer.concat(chunks, size), truncated };
      } finally {
        signal.removeEventListener("abort", abort);
        body.destroy();
      }
    });
  }

  dispose(): Promise<void> {
    return this.#disposal ??= (async () => {
      this.#controller.abort(new Error("S3 client disposed"));
      await this.#initialization?.catch(() => {});
      await Promise.allSettled(this.#operations);
      this.#sdk?.destroy();
      this.#http?.destroy();
    })();
  }
}
