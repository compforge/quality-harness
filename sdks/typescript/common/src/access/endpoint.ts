/**
 * A network access address, independent of the Service, DataSource or resource behind it.
 * @spec Endpoint contains no credentials, resource path, query or execution adapter.
 * @spec secure requires encrypted transport; the scheme determines how it is established.
 * It does not assert reachability, trust-policy approval or application authorization.
 */
export interface Endpoint {
  /** Optional identity within the declaring consumer; not a network address. */
  readonly id?: string;
  /** Human-readable description; never credentials or a signed resource URL. */
  readonly description?: string;
  /** Canonical consumer Workload name, not the remote Service or destination workload. */
  readonly workload?: string;
  /** Lowercase URI scheme without a colon; protocol metadata, not a health assertion. */
  readonly scheme?: string;
  /** Hostname or IP literal; IPv6 literals have no surrounding brackets. */
  readonly host: string;
  readonly port: number;
  /** Omission makes no encryption requirement; HTTPS/WSS/TLS endpoints require true. */
  readonly secure?: boolean;
}
