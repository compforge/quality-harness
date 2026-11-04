import type { Endpoint } from "../transport";
import { httpHandler } from "../http/handler";
import type { S3Limits, S3Target } from "./types";

/** Signing precedes the HTTP handler; routing must preserve the signed Host and TLS identity. */
export function s3Handler(target: S3Target, route: Endpoint, limits: S3Limits) {
  const endpoint = new URL(target.endpoint);
  const mapped = route.host !== endpoint.hostname
    || route.port !== Number(endpoint.port || (endpoint.protocol === "https:" ? 443 : 80));
  if (mapped && target.forcePathStyle === false) {
    throw new Error("S3 mapped transports require path-style addressing to preserve bucket and TLS identity");
  }
  const handler = httpHandler(target, route, limits);
  return { handler, destroy: () => handler.destroy() };
}
