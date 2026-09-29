export interface S3Target {
  /**
   * Complete endpoint URL, including the scheme, host and any non-default port.
   * A Kubernetes Service host includes its namespace (for example, http://s3.storage:9000).
   * The source resolves the full address; downstream clients and transports use it as-is,
   * without appending host suffixes or inferring a namespace from the consuming Service.
   */
  endpoint: string;
  region: string;
  credentials: { accessKeyId: string; secretAccessKey: string; sessionToken?: string };
  forcePathStyle?: boolean;
  /** Additional trust is explicit; TLS verification is never disabled for a tunnel. */
  ca?: string;
}

/** Root-owned policy, not product defaults. Request time includes queueing and body consumption. */
export interface S3Limits {
  concurrency: number;
  connectTimeoutMs: number;
  requestTimeoutMs: number;
}

export interface S3RequestOptions { signal?: AbortSignal }
export interface S3ObjectMetadata {
  size?: number;
  etag?: string;
  lastModified?: Date;
  contentType?: string;
  versionId?: string;
  metadata: Record<string, string>;
}

export interface S3ObjectPage {
  objects: Array<{ key: string; size?: number; etag?: string; lastModified?: Date }>;
  prefixes: string[];
  truncated: boolean;
  continuationToken?: string;
}

export interface S3BucketPage {
  buckets: Array<{ name: string; creationDate?: Date }>;
  continuationToken?: string;
}

export interface S3ObjectRead extends S3ObjectMetadata {
  bytes: Uint8Array;
  truncated: boolean;
}
