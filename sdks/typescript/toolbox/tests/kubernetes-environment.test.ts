import { expect, test } from "bun:test";
import { ClientManager, EnvironmentContext, type Service } from "@compforge/harness-common";
import { KubernetesEnvironment } from "../src/kubernetes/environment";

test("environment context acquires a shared Kubernetes client without discovery I/O", async () => {
  const clients = new ClientManager();
  const environment = { id: "cluster-a", namespace: "ns", kubeconfig: "/not/read/until/discovery", context: "test" };
  const limits = { concurrency: 2, timeoutMs: 1_000, maxBytes: 1024 };
  const provider = new KubernetesEnvironment("dev", environment, limits);
  const service: Service<KubernetesEnvironment> = {
    name: "chat",
    component: { name: "api", repository: { forge: { name: "github" }, path: "example/app" } },
    environment: provider,
    workloads: [{ name: "web", platform: "kubernetes", location: { kind: "service", name: "api" } }],
  };
  const ctx = new EnvironmentContext(service.environment, clients, performance.now() + 1_000);
  expect(service.environment.kind).toBe("kubernetes");
  const client = await ctx.clients.get(ctx.environment);
  expect(client).toBe(await ctx.clients.get(new KubernetesEnvironment("dev", { ...environment }, { ...limits })));
  expect(provider.clientKey).not.toContain(environment.kubeconfig);
  const alias = { ...environment, id: "another-label" };
  expect(new KubernetesEnvironment("alias", alias, limits).clientKey).toBe(provider.clientKey);
  expect(new KubernetesEnvironment("dev", { ...environment, context: "other" }, limits).clientKey).not.toBe(provider.clientKey);
  expect(new KubernetesEnvironment("dev", environment, { ...limits, concurrency: 1 }).clientKey).not.toBe(provider.clientKey);
  environment.context = "mutated";
  expect(provider.clientKey).not.toBe(new KubernetesEnvironment("dev", environment, limits).clientKey);
  await clients.dispose();
  expect(client.signal.aborted).toBe(true);
});


test("optional image registry is metadata, not client identity", () => {
  const access = { namespace: "app", kubeconfig: "/config", context: "dev" };
  const limits = { concurrency: 2, timeoutMs: 1_000, maxBytes: 1024 };
  const plain = new KubernetesEnvironment("dev", access, limits);
  const configured = new KubernetesEnvironment("dev", { ...access, imageRegistry: "registry.example.com/team" }, limits);
  expect(plain.imageRegistry).toBeUndefined();
  expect(configured.imageRegistry).toBe("registry.example.com/team");
  expect(configured.clientKey).toBe(plain.clientKey);
});
