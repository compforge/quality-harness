import { dataSourceKey, type DataSource } from "../datasource";
import { DirectTransport, type TcpTransport } from "../transport";
import { S3Client } from "./client";
import type { S3Limits, S3Target } from "./types";

/** Connection details are resolved by the caller; route identity includes the cluster/context, not just a transport name. */
export class S3DataSource implements DataSource<S3Client> {
  readonly key: string;
  readonly #target: S3Target;
  readonly #limits: S3Limits;
  readonly #transport: TcpTransport;
  constructor(target: S3Target, limits: S3Limits,
    route?: { key: string; transport: TcpTransport }) {
    // Snapshot configuration so key and factory cannot diverge after registration.
    this.#target = structuredClone(target);
    this.#limits = { ...limits };
    this.#transport = route?.transport ?? new DirectTransport();
    this.key = dataSourceKey("s3", { target: this.#target, limits: this.#limits,
      route: route ? { key: route.key, transport: route.transport.name } : "direct" });
  }
  createClient(signal: AbortSignal): S3Client {
    return new S3Client({ resolve: async () => this.#target,
      transports: [this.#transport] }, this.#limits, { signal });
  }
}
