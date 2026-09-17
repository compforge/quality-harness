import { expect, test } from "bun:test";
import {
  ClientManager, EnvironmentContext,
  type Client, type ClientProvider, type Component, type Environment,
  type Forge, type Host, type HostEnvironment, type Repository,
  type Service, type ServiceDataSource, type Workload,
} from "../src/index.js";

const forge: Forge = { name: "github" };
const repository: Repository = { forge, path: "example/app" };
const component: Component = { repository, name: "api" };
const environment: Environment = { name: "test" };
const workload: Workload = {
  name: "web", platform: "kubernetes",
  location: { kind: "resource", resource_kind: "Deployment", name: "api-server" },
};

test("Service binds code identity, environment and zero or more workload declarations", () => {
  const service: Service = { name: "chat", component, environment, workloads: [] };
  const deployed: Service = { ...service, workloads: [workload] };
  expect(deployed.name).not.toBe(workload.name);
  expect(deployed.component.repository.forge).toBe(forge);
  expect(deployed.component).toBe(service.component);
  expect(deployed.environment).toBe(service.environment);
  expect(service.workloads).toEqual([]);
  expect(deployed.workloads).toEqual([workload]);
  expect("clientKey" in service.environment).toBe(false);
});

test("consumers extend Service without replacing its shared topology", () => {
  interface DiagnosticService extends Service {
    readonly aliases: readonly string[];
    inspect(): string;
  }
  const service: DiagnosticService = {
    name: "chat", aliases: ["conversation"], component, environment,
    workloads: [workload], inspect: () => "ready",
  };
  const shared: Service = service;
  expect(shared.workloads[0]).toBe(workload);
  expect(service.inspect()).toBe("ready");
  // @ts-expect-error A runtime Service must identify its Environment.
  const noEnvironment: Service = { name: "chat", component, workloads: [] };
  // @ts-expect-error Component identity includes its Repository, not only a display name.
  const noRepository: Component = { name: "api" };
  // @ts-expect-error A Kubernetes Service lookup requires the common Workload shape.
  const legacyWorkload: Workload = { name: "web", discovery: { kind: "kubernetes-service", service: "api" } };
  const forbidden = () => {
    // @ts-expect-error Consumers cannot replace shared identity through the Service contract.
    shared.environment = { name: "other" };
    // @ts-expect-error Workload declarations are read-only through the Service contract.
    shared.workloads.push(workload);
  };
  void noEnvironment; void noRepository; void legacyWorkload; void forbidden;
});

test("HostEnvironment distinguishes local and SSH access without creating a client", () => {
  const local: HostEnvironment = { name: "local", kind: "host" };
  const host: Host = { name: "devbox", transport: "ssh", address: "test-builder" };
  const remote: HostEnvironment = { name: "devbox", kind: "host", host };
  const explicitLocal: Host = { name: "runner" };
  expect(local.host).toBeUndefined();
  expect(remote.host).toBe(host);
  expect("clientKey" in remote).toBe(false);
  expect(explicitLocal.transport).toBeUndefined();
  // @ts-expect-error SSH host needs an address.
  const noAddress: Host = { name: "devbox", transport: "ssh" };
  // @ts-expect-error A local host must not carry an SSH address.
  const localAddress: Host = { name: "runner", transport: "local", address: "test-builder" };
  void noAddress; void localAddress;
});

test("Service preserves accessible Environment type and shares the root client lifetime", async () => {
  let starts = 0;
  let closes = 0;
  const accessible = {
    name: "test", clientKey: "test-access",
    createClient: () => ({
      initialize: async () => { starts++; }, dispose: async () => { closes++; },
    }),
  } satisfies Environment & ClientProvider<Client>;
  const service: Service<typeof accessible> = {
    name: "chat", component, environment: accessible, workloads: [workload],
  };
  const clients = new ClientManager();
  try {
    const ctx = new EnvironmentContext(service.environment, clients, performance.now() + 1_000);
    const first = await ctx.clients.get(service.environment);
    expect(first).toBe(await ctx.clients.get(ctx.environment));
    expect(starts).toBe(1);
    expect(closes).toBe(0);
    // @ts-expect-error An execution context requires an Environment identity.
    const noIdentity = new EnvironmentContext({}, clients, performance.now());
    // @ts-expect-error ServiceDataSource ownership must use the shared Service model.
    const invalidBinding: ServiceDataSource<{ name: string }, Client> = { service: { name: "chat" }, source: accessible };
    void noIdentity; void invalidBinding;
  } finally {
    await clients.dispose();
  }
  expect(closes).toBe(1);
});
