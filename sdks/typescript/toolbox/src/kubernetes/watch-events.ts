import type { Readable } from "node:stream";
import { KubernetesError, kubernetesHttpError } from "../errors";
import type { Resource } from "./resources";

/** Bound a single event in bytes, including a line split across network packets. */
export async function consumeWatch(body: Readable, maxBytes: number, event: (phase: string, object: Resource) => void) {
  let fragments: Buffer[] = [];
  let length = 0;
  function emit() {
    if (length === 0) return;
    const line = Buffer.concat(fragments, length).toString("utf8");
    fragments = [];
    length = 0;
    let record;
    try { record = JSON.parse(line); } catch (cause) {
      throw new KubernetesError("Invalid Kubernetes WATCH JSON", { kind: "invalid_response", cause });
    }
    const phase = record?.type;
    const object = record?.object;
    if (phase === "ERROR" && Number.isInteger(object?.code)) throw kubernetesHttpError(object.code);
    if (!["ADDED", "MODIFIED", "DELETED", "BOOKMARK"].includes(phase)
      || typeof object?.metadata?.resourceVersion !== "string" || !object.metadata.resourceVersion
      || (phase !== "BOOKMARK" && (typeof object.metadata.name !== "string" || !object.metadata.name))) {
      throw new KubernetesError("Invalid Kubernetes WATCH event", { kind: "invalid_response" });
    }
    event(phase, object);
  }
  for await (const chunk of body) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    let start = 0;
    while (start < bytes.length) {
      const newline = bytes.indexOf(10, start);
      const end = newline < 0 ? bytes.length : newline;
      length += end - start;
      if (length > maxBytes) throw new KubernetesError("Kubernetes WATCH event exceeds byte limit", { kind: "limit_exceeded" });
      if (end > start) fragments.push(bytes.subarray(start, end));
      if (newline < 0) break;
      emit();
      start = end + 1;
    }
  }
  emit();
}
