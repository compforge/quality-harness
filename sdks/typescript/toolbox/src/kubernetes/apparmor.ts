import { KubernetesError, kubernetesCommandError, type ErrorKind } from "../errors";
import type { Executor } from "./executor";

export interface AppArmorUnconfinedAdmissionInput {
  namespace: string;
  serviceAccountName: string;
  image: string;
}
export interface AppArmorUnconfinedAdmissionResult {
  target: AppArmorUnconfinedAdmissionInput;
  observedAt: string;
  status: "allowed" | "denied" | "unknown";
  reason?: string;
  errorKind?: ErrorKind;
}

/**
 * @spec Probe AppArmor Unconfined admission as the supplied ServiceAccount using server-side dry-run only.
 * @rule Admission acceptance does not prove Node support. Generic RBAC/auth/transport failures remain unknown.
 * @rule Caller owns target selection, authorization and executor lifecycle; no workload or Service discovery occurs.
 * @link docs/toolbox.md
 */
export async function probeAppArmorUnconfinedAdmission(executor: Pick<Executor, "run">,
  input: AppArmorUnconfinedAdmissionInput, options: { timeoutMs: number; signal?: AbortSignal }): Promise<AppArmorUnconfinedAdmissionResult> {
  if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs <= 0 || !input.image.trim()
    || !/^[a-z0-9][a-z0-9-]*$/.test(input.namespace) || !/^[a-z0-9][a-z0-9.-]*$/.test(input.serviceAccountName)) {
    throw new KubernetesError("AppArmor admission requires namespace, ServiceAccount, image and positive timeout", { kind: "invalid_argument" });
  }
  options.signal?.throwIfAborted();
  const result: AppArmorUnconfinedAdmissionResult = { target: { ...input }, observedAt: new Date().toISOString(), status: "unknown" };
  const manifest = {
    apiVersion: "v1", kind: "Pod", metadata: { generateName: "apparmor-unconfined-probe-", namespace: input.namespace },
    spec: { serviceAccountName: input.serviceAccountName, restartPolicy: "Never", containers: [{
      name: "probe", image: input.image, securityContext: {
        appArmorProfile: { type: "Unconfined" }, allowPrivilegeEscalation: false,
        capabilities: { drop: ["ALL"] }, runAsNonRoot: true, runAsUser: 65_532, seccompProfile: { type: "RuntimeDefault" },
      },
    }] },
  };
  try {
    const execution = await executor.run(["create", "-f", "-", "--dry-run=server", "-o", "json", "--namespace", input.namespace,
      "--as", `system:serviceaccount:${input.namespace}:${input.serviceAccountName}`],
    { ...options, stdin: JSON.stringify(manifest) });
    options.signal?.throwIfAborted();
    if (execution.ok) return { ...result, status: "allowed" };
    const output = `${execution.stderr}\n${execution.stdout}`;
    if (!execution.timedOut && /apparmor/i.test(output) && /unconfined/i.test(output)
      && /(?:admission|violates\s+podsecurity)/i.test(output)
      && /(denied|forbidden|violates\s+podsecurity)/i.test(output)) {
      return { ...result, status: "denied", reason: "Admission rejected AppArmor Unconfined" };
    }
    throw kubernetesCommandError(execution);
  } catch (error) {
    options.signal?.throwIfAborted();
    const kind = error instanceof KubernetesError ? error.kind : "operation_failed";
    return { ...result, errorKind: kind, reason: `AppArmor admission could not be determined: ${kind}` };
  }
}
