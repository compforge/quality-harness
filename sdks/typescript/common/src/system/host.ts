/**
 * An optional environment access host, not necessarily the target's execution host.
 * Kubernetes access paths belong to this host's filesystem, not the Pod's.
 * SSH addresses use the caller's SSH configuration; credentials do not belong here.
 * This declaration does not imply a consumer supports SSH execution.
 */
export type Host = { readonly name: string } & (
  | { readonly transport?: "local"; readonly address?: never }
  | { readonly transport: "ssh"; readonly address: string }
);
