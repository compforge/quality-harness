import { expect, test } from "bun:test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:http";
import { KubernetesClient } from "../src/kubernetes/client";
import type { Workload } from "@compforge/harness-common";

test("native resource access preserves HTTP errors, namespace, UID and timeout/byte budgets", async () => {
  let mode = "ok";
  const paths: string[] = [];
  const server = createServer((request, response) => {
    paths.push(request.url!);
    if (mode === "timeout") return;
    if (mode === "forbidden") { response.writeHead(403); response.end("private-token"); return; }
    if (mode === "invalid") { response.end("{"); return; }
    const data = { items: [{ metadata: { namespace: "selected", name: "pod", uid: "uid" } }] };
    response.end(mode === "large" ? "x".repeat(10_000) : JSON.stringify(data));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("expected TCP fixture");
  const directory = mkdtempSync(join(tmpdir(), "workload-access-"));
  const kubeconfig = join(directory, "config.json");
  writeFileSync(kubeconfig, JSON.stringify({
    apiVersion: "v1", kind: "Config", "current-context": "fixture",
    clusters: [{ name: "fixture", cluster: { server: "http://127.0.0.1:" + address.port } }],
    contexts: [{ name: "fixture", context: { cluster: "fixture", user: "fixture" } }],
    users: [{ name: "fixture", user: {} }],
  }));
  const client = new KubernetesClient({ namespace: "default", kubeconfig },
    undefined, undefined, { concurrency: 1, timeoutMs: 100, maxBytes: 1024 });
  const workload: Workload = { platform: "kubernetes", name: "api", namespace: "selected",
    location: { kind: "labels", labels: { app: "api" } } };
  try {
    const instances = await client.resolveWorkload(workload, "cluster-a/context-a");
    expect(instances[0]).toMatchObject({ namespace: "selected", uid: "uid", pod: "pod" });
    expect(paths[0]).toBe("/api/v1/namespaces/selected/pods?labelSelector=app%3Dapi");
    expect(instances[0].environment).toBe("cluster-a/context-a");
    for (const [selected, kind] of [
      ["forbidden", "permission_denied"], ["invalid", "invalid_response"],
      ["large", "limit_exceeded"], ["timeout", "timeout"],
    ]) {
      mode = selected;
      await expect(client.resolveWorkload(workload, "cluster-a/context-a")).rejects.toMatchObject({ kind });
    }
  } finally {
    await client.dispose();
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(directory, { recursive: true, force: true });
  }
});
