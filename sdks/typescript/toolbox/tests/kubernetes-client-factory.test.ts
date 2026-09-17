import { expect, test } from "bun:test";
import { ClientManager, EnvironmentContext } from "@compforge/harness-common";
import { KubernetesClientFactory } from "../src/kubernetes/client-factory";

test("environment context acquires a shared Kubernetes client without discovery I/O", async () => {
  const clients = new ClientManager();
  const environment = { id: "cluster-a", namespace: "ns", kubeconfig: "/not/read/until/discovery", context: "test" };
  const ctx = new EnvironmentContext(environment, clients, performance.now() + 1_000);
  const limits = { concurrency: 2, timeoutMs: 1_000, maxBytes: 1024 };
  const factory = new KubernetesClientFactory(ctx.environment, limits);
  const client = await ctx.clients.get(factory);
  expect(client).toBe(await ctx.clients.get(new KubernetesClientFactory({ ...environment }, { ...limits })));
  expect(factory.key).not.toContain(environment.kubeconfig);
  const alias = { ...environment, id: "another-label" };
  expect(new KubernetesClientFactory(alias, limits).key).toBe(factory.key);
  expect(new KubernetesClientFactory({ ...environment, context: "other" }, limits).key).not.toBe(factory.key);
  expect(new KubernetesClientFactory(environment, { ...limits, concurrency: 1 }).key).not.toBe(factory.key);
  environment.context = "mutated";
  expect(factory.key).not.toBe(new KubernetesClientFactory(environment, limits).key);
  await clients.dispose();
  expect(client.signal.aborted).toBe(true);
});
