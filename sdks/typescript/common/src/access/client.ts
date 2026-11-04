/**
 * Managed access lifecycle, implemented by protocol clients and resource-owning transports.
 * @spec Initialization failure still requires disposal of every partially created resource.
 * @spec Successful disposal means owned work has ended; borrowed dependencies belong to their manager.
 * @rule A timeout waiting for cleanup is a cleanup failure, never proof that resources were released.
 */
export interface Client {
  initialize(): Promise<void>;
  dispose(): Promise<void>;
}
