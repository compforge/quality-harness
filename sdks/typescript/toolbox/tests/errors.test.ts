import { expect, test } from "bun:test";
import { kubernetesCommandError, ToolboxError } from "../src/errors";

test("kubectl resource requests share the HTTP error contract", () => {
  for (const [reason, kind, code] of [
    ["NotFound", "resource_not_found", 404], ["Forbidden", "permission_denied", 403],
    ["Unauthorized", "authentication_failed", 401], ["TooManyRequests", "limit_exceeded", 429],
    ["ServerTimeout", "timeout", 504],
  ] as const) {
    const error = kubernetesCommandError({ stderr: `Error from server (${reason}): private detail`, timedOut: false });
    expect(error).toBeInstanceOf(ToolboxError);
    expect(error).toMatchObject({ kind, code });
    expect(error.message).not.toContain("private detail");
  }
});

test("timeouts and unknown failures cannot be mistaken for a missing resource", () => {
  expect(kubernetesCommandError({ stderr: "Error from server (NotFound): stale output", timedOut: true }).kind).toBe("timeout");
  for (const stderr of ["kubectl: command not found", "connection timeout", "resource not found", "Error from server (InternalError): not found"]) {
    expect(kubernetesCommandError({ stderr, timedOut: false }).kind).toBe("operation_failed");
  }
});
