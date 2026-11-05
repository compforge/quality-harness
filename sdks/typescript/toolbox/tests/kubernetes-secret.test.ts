import { expect, test } from "bun:test";
import { ClientManager, type ClientProvider } from "@compforge/harness-common";
import { KubernetesError } from "../src/errors";
import { KubernetesSecretClient, KubernetesSecretDataSource, type KubernetesSecretAccess } from "../src/kubernetes/secret-datasource";

const target = { namespace: "demo", name: "runtime-config" };
function fixture(get: KubernetesSecretAccess["get"], key = "cluster-a") {
  let initialized = 0, disposed = 0;
  const provider: ClientProvider<KubernetesSecretAccess> = { clientKey: key, createClient: () => ({
    get, initialize: async () => { initialized++; }, dispose: async () => { disposed++; },
  }) };
  return { provider, get initialized() { return initialized; }, get disposed() { return disposed; } };
}

test("Secret datasource declares immutable identity, shares the root client and projects only selected keys", async () => {
  let reads = 0;
  const access = fixture(async (ref, options) => {
    expect(ref).toEqual({ apiVersion: "v1", resource: "secrets", ...target });
    expect(options?.timeoutMs).toBe(1000);
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    return { metadata: { resourceVersion: String(++reads) }, data: {
      NETWORKS: Buffer.from(`revision=${reads}`).toString("base64"), EMPTY: "", TOKEN: Buffer.from("private-token").toString("base64"),
    } };
  });
  const keys = ["NETWORKS", "EMPTY", "MISSING"], mutable = { ...target };
  const source = new KubernetesSecretDataSource(access.provider, mutable, keys);
  const equivalent = new KubernetesSecretDataSource(access.provider, target, [...keys]);
  expect(access.initialized).toBe(0); expect(reads).toBe(0);
  keys.push("TOKEN"); mutable.name = "different";
  expect(source.target).toEqual(target); expect(source.keys).not.toContain("TOKEN");
  expect(equivalent.clientKey).toBe(source.clientKey);
  expect(new KubernetesSecretDataSource(access.provider, { ...target, namespace: "other" }, source.keys).clientKey).not.toBe(source.clientKey);
  expect(new KubernetesSecretDataSource(fixture(async () => ({}), "cluster-b").provider,
    target, source.keys).clientKey).not.toBe(source.clientKey);
  const clients = new ClientManager();
  try {
    const client = await clients.get(source);
    expect(await clients.get(equivalent)).toBe(client); expect(access.initialized).toBe(1);
    for (let revision = 1; revision <= 2; revision++) {
      const result = await client.read({ timeoutMs: 1000 });
      expect(result).toMatchObject({ values: { NETWORKS: `revision=${revision}`, EMPTY: "" }, missingKeys: ["MISSING"], resourceVersion: String(revision) });
      expect(Number.isNaN(Date.parse(result.observedAt))).toBe(false);
      expect(JSON.stringify(result)).not.toContain("private-token");
    }
    await client.dispose();
    expect(access.disposed).toBe(0);
    await expect(client.read()).rejects.toThrow("disposed");
  } finally { await clients.dispose(); }
  expect(access.disposed).toBe(1);
});

for (const keys of [[], [""], ["A", "A"], ["a/b"]]) {
  test(`Secret key selection rejects invalid declarations: ${JSON.stringify(keys)}`, () => {
    expect(() => new KubernetesSecretDataSource(fixture(async () => ({})).provider, target, keys)).toThrow(KubernetesError);
  });
}

test("Secret datasource rejects unscoped targets before any access", () => {
  const access = fixture(async () => { throw new Error("unexpected access"); });
  for (const invalid of [{ namespace: "", name: "x" }, { namespace: "demo", name: "../x" }]) {
    expect(() => new KubernetesSecretDataSource(access.provider, invalid, ["A"])).toThrow(KubernetesError);
  }
  expect(access.initialized).toBe(0);
});

for (const encoded of ["%%%credential%%%", "/w=="]) {
  test(`Secret decoding rejects malformed values without retaining them: ${encoded === "/w==" ? "utf8" : "base64"}`, async () => {
    const client = new KubernetesSecretClient(["RULE"], new AbortController().signal, async () => ({ target,
      access: { get: async () => ({ data: { RULE: encoded } }) },
    }));
    await client.initialize();
    try { await client.read(); throw new Error("expected invalid response"); }
    catch (error) {
      expect(error).toBeInstanceOf(KubernetesError);
      expect((error as KubernetesError).kind).toBe("invalid_response");
      expect(String(error)).not.toContain(encoded);
      expect((error as Error).cause).toBeUndefined();
    } finally { await client.dispose(); }
  });
}

for (const kind of ["permission_denied", "resource_not_found"] as const) {
  test(`Secret failures preserve API classification: ${kind}`, async () => {
    const error = new KubernetesError("resource request failed", { kind });
    const client = new KubernetesSecretClient(["RULE"], new AbortController().signal, async () => ({ target,
      access: { get: async () => { throw error; } },
    }));
    await client.initialize();
    await expect(client.read()).rejects.toBe(error);
    await client.dispose();
  });
}

for (const cancel of ["read", "root", "dispose"] as const) {
  test(`Secret reads propagate cancellation and disposal drains work: ${cancel}`, async () => {
    const root = new AbortController(), readAbort = new AbortController();
    let started!: () => void;
    const reading = new Promise<void>(resolve => { started = resolve; });
    let finished = false;
    const client = new KubernetesSecretClient(["RULE"], root.signal, async () => ({ target, access: {
      get: async (_ref, options) => {
        started();
        try { return await new Promise<never>((_resolve, reject) => options!.signal!.addEventListener("abort", () => reject(options!.signal!.reason), { once: true })); }
        finally { finished = true; }
      },
    } }));
    await client.initialize();
    const pending = client.read({ signal: readAbort.signal });
    const outcome = pending.then(() => undefined, error => error);
    await reading;
    if (cancel === "read") readAbort.abort(new Error("read cancelled"));
    else if (cancel === "root") root.abort(new Error("root cancelled"));
    else await client.dispose();
    expect(await outcome).toBeInstanceOf(Error);
    await client.dispose();
    expect(finished).toBe(true);
  });
}
