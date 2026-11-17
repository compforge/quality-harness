import { HttpRequestTrace } from "./diagnostics";
import { Agent as HttpAgent } from "node:http";
import { Agent as HttpsAgent } from "node:https";
import { isIP } from "node:net";
import { checkServerIdentity } from "node:tls";
import { NodeHttpHandler } from "@smithy/node-http-handler";
import type { Endpoint } from "../transport";
import type { HttpTarget, HttpLimits } from "./types";

/** @rule Routing changes the dial address, never the HTTP Host or certificate identity. */
export function httpHandler(target: HttpTarget, route: Endpoint, limits: Omit<HttpLimits, "maxResponseBytes">) {
  const endpoint = new URL(target.endpoint);
  const hostname = endpoint.hostname.replace(/^\[|\]$/g, "");
  const mapped = route.host !== hostname || route.port !== Number(endpoint.port || (endpoint.protocol === "https:" ? 443 : 80));
  const pool = { keepAlive: true, maxSockets: limits.concurrency, maxTotalSockets: limits.concurrency };
  const httpAgent = new HttpAgent(pool);
  const httpsAgent = new HttpsAgent({ ...pool, ca: target.ca, rejectUnauthorized: true,
    ...(mapped ? { servername: isIP(hostname) ? "" : hostname,
      checkServerIdentity: (_host: string, certificate: Parameters<typeof checkServerIdentity>[1]) => checkServerIdentity(hostname, certificate) } : {}) });
  const engine = new NodeHttpHandler({ httpAgent, httpsAgent,
    connectionTimeout: limits.connectTimeoutMs, requestTimeout: limits.requestTimeoutMs,
    throwOnRequestTimeout: true });
  // Handler routing works on Bun too; its Agent.createConnection is not a routing hook.
  return {
    async handle(request: Parameters<NodeHttpHandler["handle"]>[0], options: Parameters<NodeHttpHandler["handle"]>[1]) {
      const trace = HttpRequestTrace.current();
      const routed = mapped ? request.clone() : request;
      if (mapped) { routed.hostname = route.host; routed.port = route.port; }
      trace?.dispatch(request, {
        host: routed.hostname, port: routed.port ?? (routed.protocol === "https:" ? 443 : 80),
      }, request.protocol === "https:" ? httpsAgent : httpAgent);
      const result = await engine.handle(routed, options);
      trace?.headers();
      return result;
    },
    destroy() { engine.destroy(); httpAgent.destroy(); httpsAgent.destroy(); },
  };
}
