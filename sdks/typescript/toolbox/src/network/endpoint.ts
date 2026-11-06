import type { Endpoint } from "@compforge/harness-common";
import { isIP } from "node:net";

const URL_DEFAULTS = new Map<string, { port?: number; secure: boolean }>([
  ["http", { port: 80, secure: false }], ["https", { port: 443, secure: true }],
  ["ws", { port: 80, secure: false }], ["wss", { port: 443, secure: true }],
  ["ftp", { port: 21, secure: false }], ["tls", { secure: true }],
]);

/** @spec Validation performs no DNS or I/O and never includes the supplied address in errors. */
export function validateEndpoint(endpoint: Endpoint): void {
  if (typeof endpoint.host !== "string" || !endpoint.host || /[\s/\\?#@%\[\]]/.test(endpoint.host)
    || (endpoint.host.includes(":") && isIP(endpoint.host) !== 6)) {
    throw new Error("Endpoint host must be a hostname/IP, with IPv6 unbracketed and no URL components");
  }
  if (!Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535) {
    throw new Error("Endpoint port must be an integer between 1 and 65535");
  }
  if (endpoint.scheme !== undefined && !/^[a-z][a-z0-9+.-]*$/.test(endpoint.scheme)) {
    throw new Error("Endpoint scheme must be lowercase and contain no colon");
  }
  if (endpoint.secure !== undefined && typeof endpoint.secure !== "boolean") throw new Error("Endpoint secure must be boolean");
  const defaults = endpoint.scheme ? URL_DEFAULTS.get(endpoint.scheme) : undefined;
  if (defaults && endpoint.scheme !== "ftp" && (endpoint.secure ?? false) !== defaults.secure) {
    throw new Error("Endpoint secure conflicts with its scheme; HTTPS/WSS/TLS require encryption");
  }
}

/**
 * @spec Extract only the network address from an absolute URL; discard credentials, path, query and fragment.
 * @spec HTTP(S), WS(S) and FTP have default ports. TLS requires a port and secure=true; other schemes require a port and leave secure unspecified.
 * @why A signed resource URL must not leak its signature into connectivity commands or evidence.
 */
export function endpointFromUrl(value: string | URL): Endpoint {
  let url: URL;
  try { url = new URL(value); } catch { throw new Error("Endpoint requires a valid absolute network URL"); }
  const scheme = url.protocol.slice(0, -1);
  const defaults = URL_DEFAULTS.get(scheme);
  const port = url.port ? Number(url.port) : defaults?.port;
  if (port === undefined) throw new Error("Endpoint URL requires an explicit port for this scheme");
  const endpoint: Endpoint = {
    scheme, host: url.hostname.replace(/^\[|\]$/g, ""), port,
    ...(defaults ? { secure: defaults.secure } : {}),
  };
  validateEndpoint(endpoint);
  return endpoint;
}
