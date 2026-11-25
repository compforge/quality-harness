import { ListWatch, type Watch } from "@kubernetes/client-node";
import { channel } from "node:diagnostics_channel";
import { setTimeout as delay } from "node:timers/promises";
import { KubernetesError } from "../errors";
import { resourcePath, type Resource, type ResourceAccess, type ResourceCacheQuery, type ResourceList } from "./resources";

const synchronization = channel("harness-toolbox.kubernetes.cache");

export interface SyncedCollection {
  dispose(): Promise<void>;
  snapshot(): ResourceList | undefined;
}

export type WatchCollection = (version: string, signal: AbortSignal, opened: () => void,
  event: (phase: string, object: Resource) => void) => Promise<void>;

async function listCollection(access: ResourceAccess, query: ResourceCacheQuery, signal: AbortSignal) {
  let collection: ResourceList | undefined;
  const tokens = new Set<string>();
  let token: string | undefined;
  do {
    const page = await access.list({ ...query, limit: 100, continue: token }, { signal });
    const rv = page.metadata?.resourceVersion;
    if (typeof rv !== "string" || !rv || (collection && collection.metadata?.resourceVersion !== rv)
      || page.items.some(item => typeof item.metadata?.name !== "string" || !item.metadata.name)) {
      throw new KubernetesError("Invalid Kubernetes LIST snapshot", { kind: "invalid_response" });
    }
    if (!collection) {
      const metadata = { ...page.metadata };
      delete metadata.continue;
      delete metadata.remainingItemCount;
      collection = { ...page, metadata, items: [] };
    }
    for (const item of page.items) collection.items.push(item);
    token = page.metadata?.continue;
    if (token) {
      if (typeof token !== "string" || tokens.has(token)) {
        throw new KubernetesError("Invalid Kubernetes LIST continuation", { kind: "invalid_response" });
      }
      tokens.add(token);
    }
  } while (token);
  return collection;
}

/**
 * @spec A complete LIST is published only while its WATCH is connected. A 410 replaces the collection.
 * @why One supervisor owns retries and shutdown; ListWatch owns indexing, bookmarks and version advancement.
 */
export function startResourceCache(access: ResourceAccess, config: Watch["config"], query: ResourceCacheQuery,
  parent: AbortSignal, watch: WatchCollection): SyncedCollection {
  const path = resourcePath(query);
  const controller = new AbortController();
  const signal = AbortSignal.any([parent, controller.signal]);
  let ready = false;
  let collectionFields: ResourceList = { items: [] };
  let informer: ListWatch<Resource> | undefined;
  const driver: Watch = {
    config,
    async watch(_path, params, event) {
      // Keep start() pending for the stream lifetime so dispose can join authentication, LIST and WATCH.
      const request = new AbortController();
      await watch(String(params.resourceVersion), AbortSignal.any([signal, request.signal]), () => {
        ready = true;
        if (synchronization.hasSubscribers) synchronization.publish({ path, state: "ready" });
      }, event);
      return request;
    },
  };
  async function synchronize() {
    let retryMs = 250;
    while (!signal.aborted) {
      const started = Date.now();
      let failure: unknown;
      try {
        if (!informer) {
          informer = new ListWatch(path, driver, async () => {
            const collection = await listCollection(access, query, signal);
            collectionFields = { ...collection, items: [] };
            return collection;
          }, false);
          informer.on("error", error => { throw error; });
        }
        await informer.start();
      } catch (error) {
        if (signal.aborted) break;
        failure = error;
        if (error instanceof KubernetesError && error.code === 410) informer = undefined;
      }
      if (signal.aborted) break;
      ready = false;
      if (Date.now() - started >= 30_000) retryMs = 250;
      if (synchronization.hasSubscribers) synchronization.publish({ path, state: "retrying", retryMs,
        reason: failure instanceof KubernetesError ? failure.kind : failure ? "sync_failed" : "watch_ended",
        code: failure instanceof KubernetesError ? failure.code : undefined });
      await delay(retryMs, undefined, { signal }).catch(() => {});
      retryMs = Math.min(retryMs * 2, 30_000);
    }
    await informer?.stop();
    informer = undefined;
  }
  const done = synchronize();
  return {
    snapshot() {
      if (signal.aborted || !ready || !informer) return undefined;
      return structuredClone({ ...collectionFields,
        metadata: { ...collectionFields.metadata, resourceVersion: informer.latestResourceVersion() },
        items: [...informer.list(query.namespace)] });
    },
    dispose() { controller.abort(); return done; },
  };
}
