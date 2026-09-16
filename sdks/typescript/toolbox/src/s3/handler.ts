import { Agent as HttpAgent } from "node:http";
import { Agent as HttpsAgent } from "node:https";
import { NodeHttpHandler } from "@smithy/node-http-handler";
import type { Endpoint } from "../transport";
import type { S3Limits, S3Target } from "./types";

/** Routing changes only the socket destination, never the signed HTTP Host or TLS identity. */
export function s3Handler(target: S3Target, route: Endpoint, limits: S3Limits) {
  const endpoint = new URL(target.endpoint);
  const mapped = route.host !== endpoint.hostname
    || route.port !== Number(endpoint.port || (endpoint.protocol === "https:" ? 443 : 80));
  if (mapped && target.forcePathStyle === false) {
    throw new Error("S3 mapped transports require path-style addressing to preserve bucket and TLS identity");
  }
  const pool = { keepAlive: true, maxSockets: limits.concurrency, maxTotalSockets: limits.concurrency };
  const httpAgent = new HttpAgent(pool);
  const httpsAgent = new HttpsAgent({ ...pool, ca: target.ca, rejectUnauthorized: true,
    ...(mapped ? { servername: endpoint.hostname } : {}) });
  const transport = new NodeHttpHandler({ httpAgent, httpsAgent,
    connectionTimeout: limits.connectTimeoutMs, requestTimeout: limits.requestTimeoutMs,
    throwOnRequestTimeout: true });
  // SDK signing precedes this handler. Rewrite the dial address only, leaving signed headers intact.
  // A handler adapter also works on Bun, whose HTTP implementation ignores Agent.createConnection.
  const handler = {
    handle(request: Parameters<NodeHttpHandler["handle"]>[0], options: Parameters<NodeHttpHandler["handle"]>[1]) {
      if (!mapped) return transport.handle(request, options);
      const routed = request.clone();
      routed.hostname = route.host;
      routed.port = route.port;
      return transport.handle(routed, options);
    },
    destroy() { transport.destroy(); },
  };
  // The SDK may treat injected agents as externally owned; this client owns both pools explicitly.
  return { handler, destroy: () => { handler.destroy(); httpAgent.destroy(); httpsAgent.destroy(); } };
}
