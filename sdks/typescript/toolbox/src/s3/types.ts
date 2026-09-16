export interface S3Target {
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
