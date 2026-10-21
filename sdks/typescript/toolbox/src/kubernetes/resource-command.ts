import { KubernetesError } from "../errors";
import type { ExecResult, RunOptions } from "./executor";
import { resourceRoute, type ResourceAccess } from "./resources";

export interface ResourceGetCommand {
  resource: string;
  name?: string;
  selector?: string;
}

/** Exact compatibility subset only; watch, discovery, other formats and arbitrary flags stay with kubectl. */
export function resourceGetCommand(command: readonly string[]): ResourceGetCommand | undefined {
  if (command[0] !== "get" || !command[1]) return;
  try { resourceRoute(command[1]); } catch { return; }
  const request: ResourceGetCommand = { resource: command[1] };
  let json = false;
  for (let index = 2; index < command.length; index++) {
    const argument = command[index];
    if (argument === "-o" && command[index + 1] === "json" && !json) { json = true; index++; }
    else if (argument === "-l" && command[index + 1] && !command[index + 1].startsWith("-") && request.selector === undefined) {
      request.selector = command[++index];
    } else if (argument && /^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$/.test(argument) && request.name === undefined) request.name = argument;
    else return;
  }
  return json ? request : undefined;
}

/** Keep native request provenance explicit; never claim that kubectl executed this request. */
export async function runResourceGet(
  access: () => ResourceAccess, namespace: string, request: ResourceGetCommand, options?: RunOptions,
): Promise<ExecResult> {
  const command = ["kubernetes-api", "get", "-n", namespace, request.resource,
    ...(request.name ? [request.name] : []), ...(request.selector ? ["-l", request.selector] : [])];
  const started = performance.now();
  const result: ExecResult = { command, ok: false, exitCode: null, stdout: "", stderr: "", timedOut: false, durationMs: 0 };
  try {
    const resource = await access().get(namespace, request.resource, request.name, request.selector, options);
    options?.signal?.throwIfAborted();
    const json = JSON.stringify(resource);
    options?.onStdoutBytes?.(new TextEncoder().encode(json));
    options?.onStdout?.(json);
    result.stdout = options?.collectStdout === false ? "" : json;
    result.ok = true;
    result.exitCode = 0;
  } catch (error) {
    const reason = error instanceof KubernetesError && typeof error.code === "number"
      ? ({ 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 408: "Timeout",
        429: "TooManyRequests", 504: "ServerTimeout" } as Record<number, string>)[error.code] : undefined;
    result.timedOut = error instanceof KubernetesError && error.kind === "timeout";
    result.stderr = options?.signal?.aborted ? "[aborted]" : reason
      ? `Error from server (${reason}): Kubernetes resource request failed`
      : error instanceof Error ? error.message : "Kubernetes resource request failed";
  } finally { result.durationMs = performance.now() - started; }
  return result;
}
