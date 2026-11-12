import { expect, test } from "bun:test";
import { probeAppArmorUnconfinedAdmission } from "../src/kubernetes/apparmor";
import type { ExecResult } from "../src/kubernetes/executor";

const target = { namespace: "demo", serviceAccountName: "controller", image: "runtime:v1" };
const response = (stderr: string, ok = false): ExecResult => ({ ok, stderr, stdout: "", exitCode: ok ? 0 : 1, timedOut: false, durationMs: 1, command: [] });
test("AppArmor admission uses caller parameters and dry-run only", async () => {
  let calls = 0;
  const result = await probeAppArmorUnconfinedAdmission({ run: async (args, options) => {
    calls++;
    expect(args).toEqual(["create", "-f", "-", "--dry-run=server", "-o", "json", "--namespace", "demo", "--as", "system:serviceaccount:demo:controller"]);
    expect(options?.timeoutMs).toBe(5000);
    const manifest = JSON.parse(String(options?.stdin));
    expect(manifest.metadata.namespace).toBe("demo");
    expect(manifest.spec.serviceAccountName).toBe("controller");
    expect(manifest.spec.containers[0]).toMatchObject({ image: "runtime:v1", securityContext: { appArmorProfile: { type: "Unconfined" } } });
    return response("", true);
  } }, target, { timeoutMs: 5000 });
  expect(calls).toBe(1); expect(result).toMatchObject({ status: "allowed", target });
});

for (const [message, expected] of [
  ['admission webhook denied request: AppArmor Unconfined is forbidden', 'denied'],
  ['violates PodSecurity restricted: appArmorProfile.type Unconfined', 'denied'],
  ['Error from server (Forbidden): cannot impersonate serviceaccount apparmor-unconfined', 'unknown'],
  ['Error from server (Forbidden): cannot create pods', 'unknown'],
  ['Error from server (NotFound): serviceaccount missing', 'unknown'],
  ['connection refused: secret', 'unknown'],
] as const) test(`AppArmor only classifies explicit admission decisions: ${message}`, async () => {
  const result = await probeAppArmorUnconfinedAdmission({ run: async () => response(message) }, target, { timeoutMs: 5000 });
  expect(result.status).toBe(expected);
  expect(JSON.stringify(result)).not.toContain(message);
});

test("timeout overrides apparent rejection and parent cancellation is thrown", async () => {
  const result = await probeAppArmorUnconfinedAdmission({ run: async () => ({ ...response("admission denied AppArmor Unconfined"), timedOut: true }) }, target, { timeoutMs: 5000 });
  expect(result).toMatchObject({ status: "unknown", errorKind: "timeout" });
  const abort = new AbortController();
  await expect(probeAppArmorUnconfinedAdmission({ run: async () => { abort.abort(new Error("stop")); throw abort.signal.reason; } }, target,
    { timeoutMs: 5000, signal: abort.signal })).rejects.toThrow("stop");
});

test("invalid configuration fails before invoking the executor", async () => {
  await expect(probeAppArmorUnconfinedAdmission({ run: async () => { throw new Error("must not run"); } }, { ...target, namespace: "../demo" },
    { timeoutMs: 5000 })).rejects.toThrow("positive timeout");
});
