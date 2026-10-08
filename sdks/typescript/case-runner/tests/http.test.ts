import { expect, test } from "bun:test";
import { caseHash } from "@compforge/spec-case/model";
import { executeHttpCase, judgeHttpResponse, validatePreparedHttpCase, type PreparedHttpCase } from "../src/index.js";
const item: PreparedHttpCase = { case: { id: "download", input: { protocol: "http", method: "GET", headers: { Range: "bytes=0-1023" } },
  judge: { e2e: { http: { status: [200, 206], contentType: "application/octet-stream" } } } },
  target: { url: "https://portal.test/a%2Fb?signature=SECRET", headers: { Authorization: "Bearer private" },
    alternatives: [{ id: "internal", url: "http://platform:3000/a%2Fb?signature=SECRET" }] } };
const budget = { timeoutMs: 1000, maxResponseBytes: 1024 };

test("prepared targets preserve signatures, canonical identity and the caller's execution channel", async () => {
  const hash = caseHash(item.case);
  const result = await executeHttpCase(item, "internal", budget, new AbortController().signal, async (request, signal) => {
    expect(request.url).toBe(item.target.alternatives![0]!.url);
    expect(request.headers.authorization).toBe("Bearer private");
    expect(request.headers.range).toBe("bytes=0-1023");
    expect(request.followRedirects).toBe(false);
    expect(request.maxResponseBytes).toBe(1024);
    expect(signal.aborted).toBe(false);
    return { response: { statusCode: 206, contentType: "Application/Octet-Stream; charset=binary", captureComplete: true }, evidence: "caller-owned" };
  });
  expect(result.judgment.status).toBe("passed");
  expect(result.observation.evidence).toBe("caller-owned");
  expect(caseHash({ ...item, target: { url: "https://other.test/new?signature=fresh" } }.case)).toBe(hash);
});

test("missing judgment observes only; incomplete and unexpected responses never pass", () => {
  expect(judgeHttpResponse({ response: { captureComplete: true, statusCode: 200 } }).status).toBe("observed");
  expect(judgeHttpResponse({ response: { captureComplete: false, statusCode: 200 } }, { status: [200] }).status).toBe("failed");
  expect(judgeHttpResponse({ response: { captureComplete: true, statusCode: 403 } }, { status: [200] }).mismatches).toEqual(["status"]);
});

test("invalid targets, unsupported protocols and cancellation do not invoke the channel", async () => {
  for (const url of ["file:///etc/passwd", "https://user:secret@portal.test"]) expect(() => validatePreparedHttpCase({ ...item, target: { url } })).toThrow();
  let calls = 0;
  const execute = async () => { calls++; return { response: { captureComplete: true, statusCode: 200 } }; };
  const controller = new AbortController(); controller.abort();
  await expect(executeHttpCase(item, "primary", budget, controller.signal, execute)).rejects.toThrow();
  await expect(executeHttpCase({ ...item, case: { ...item.case, input: { protocol: "tcp" } as never } }, "primary", budget, new AbortController().signal, execute)).rejects.toThrow();
  await expect(executeHttpCase(item, "missing", budget, new AbortController().signal, execute)).rejects.toThrow();
  expect(calls).toBe(0);
});


test("origin-relative paths remain on the selected target and malformed paths never execute", async () => {
  let calls = 0;
  const execute = async (request: { url: string }) => {
    calls++;
    expect(request.url).toBe("http://platform:3000/health?probe=ready");
    return { response: { captureComplete: true, statusCode: 200, contentType: "application/octet-stream" } };
  };
  const preparePath = (path: string): PreparedHttpCase => ({ ...item, case: { ...item.case, input: { ...item.case.input, path } } });
  await executeHttpCase(preparePath("/health?probe=ready"), "internal", budget, new AbortController().signal, execute);
  for (const path of ["//other.test", "/\t/other.test", "/\\other.test"]) {
    await expect(executeHttpCase(preparePath(path), "internal", budget, new AbortController().signal, execute)).rejects.toThrow();
  }
  expect(calls).toBe(1);
});
