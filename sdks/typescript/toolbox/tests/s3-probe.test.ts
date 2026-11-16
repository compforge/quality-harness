import { expect, test } from "bun:test";
import { S3Error, probeS3ReadWrite, type S3ReadWriteProbe, type S3ReadWriteClient } from "../src/s3";

function fixture() {
  const calls: { operation: string; bucket: string; key: string; versionId?: string }[] = [];
  const controller = new AbortController();
  const result: S3ReadWriteProbe = { bucket: "bucket", startedAt: new Date().toISOString(), status: "unavailable", steps: [] };
  const client: S3ReadWriteClient = {
    putObject: async (bucket, key, bytes, options) => {
      calls.push({ operation: "put", bucket, key });
      expect(Buffer.from(bytes).toString()).toBe("hello world");
      expect(options.maxBytes).toBe(11);
      return { versionId: "v1", etag: "etag-1", response: { httpStatusCode: 200, requestId: "put-1", attempts: 1 } };
    },
    presignGetObject: async (bucket, key, options) => {
      calls.push({ operation: "presign", bucket, key, versionId: options.versionId });
      expect(options.expiresIn).toBe(60);
      return "http://s3.test/hello.txt?X-Amz-Signature=SECRET";
    },
    deleteObject: async (bucket, key, options) => {
      expect(options?.signal?.aborted).toBe(false);
      calls.push({ operation: "delete", bucket, key, versionId: options?.versionId });
    },
  };
  const input = { client, cleanupTimeoutMs: 10_000, presignExpiresInSeconds: 60, secrets: ["SECRET"], bucket: "bucket", prefix: "customer/prefix/", signal: controller.signal,
    checkpoint: (snapshot: Readonly<S3ReadWriteProbe>) => { Object.assign(result, snapshot); },
    download: async (url: string, _signal: AbortSignal) => {
      expect(url).toContain("X-Amz-Signature=SECRET");
      calls.push({ operation: "get", bucket: "bucket", key: result.key!, versionId: result.versionId });
      return { status: 200, headers: {} as Record<string, string>, body: Buffer.from("hello world") };
    } };
  return { calls, controller, result, client, input };
}

test("S3 checks only a unique hello.txt key under the configured prefix and cleans the exact uploaded version", async () => {
  const a = fixture(); const b = fixture();
  await probeS3ReadWrite(a.input); await probeS3ReadWrite(b.input);
  expect(a.result.key).toMatch(/^customer\/prefix\/[a-f0-9-]+\/hello.txt$/);
  expect(a.result.key).not.toBe(b.result.key);
  expect(a.result.status).toBe("passed");
  expect(a.result.steps.map(step => step.status)).toEqual(["passed", "passed", "passed", "passed"]);
  expect(a.calls.map(call => call.operation)).toEqual(["put", "presign", "get", "delete"]);
  expect(new Set(a.calls.map(call => call.key))).toEqual(new Set([a.result.key!]));
  expect(a.calls.slice(1).map(call => call.versionId)).toEqual(["v1", "v1", "v1"]);
  expect(a.result.steps[2]!.bytes).toBe(11);
  expect(a.result.steps[0]!.response).toMatchObject({ httpStatus: 200, requestId: "put-1", etag: "etag-1", versionId: "v1" });
  expect(a.result.steps[2]!.response?.body).toBe("hello world");
});

for (const bytes of ["hello world\n", "hello worlx", "", "login page"]) test(`download bytes are validated exactly: ${JSON.stringify(bytes)}`, async () => {
  const f = fixture();
  f.input.download = async () => ({ status: 200, headers: {}, body: Buffer.from(bytes) });
  await probeS3ReadWrite(f.input);
  expect(f.result.status).toBe("failed");
  expect(f.result.steps[2]!.reason).toContain("不一致");
  expect(f.result.steps[3]!.status).toBe("passed");
});

test("PUT denial prevents GET and cleanup; SDK facts omit echoed signed URLs", async () => {
  const f = fixture();
  f.client.putObject = async () => { throw Object.assign(new Error("https://s3/?token=SECRET"), { name: "AccessDenied", $metadata: { httpStatusCode: 403, requestId: "r1" } }); };
  await probeS3ReadWrite(f.input);
  expect(f.result.status).toBe("failed");
  expect(f.result.steps.map(step => step.status)).toEqual(["failed", "not-run", "not-run", "not-run"]);
  expect(f.result.steps[0]!.failure).toMatchObject({ code: "AccessDenied", httpStatus: 403, requestId: "r1" });
  expect(JSON.stringify(f.result)).not.toContain("SECRET");
  expect(f.calls).toEqual([]);
});

test("failed or cancelled downloads still clean up using a live cleanup deadline", async () => {
  for (const cancel of [false, true]) {
    const f = fixture();
    f.input.download = async () => { if (cancel) f.controller.abort(); throw Object.assign(new Error("failure"), { name: cancel ? "AbortError" : "NoSuchKey" }); };
    await probeS3ReadWrite(f.input);
    expect(f.result.status).toBe(cancel ? "cancelled" : "failed");
    expect(f.result.steps[3]!.status).toBe("passed");
    expect(f.calls.map(call => call.operation)).toEqual(["put", "presign", "delete"]);
  }
});

test("cleanup denial retains successful read/write results and the exact residual object identity", async () => {
  const f = fixture();
  f.client.deleteObject = async () => { throw Object.assign(new Error("denied"), { name: "AccessDenied", $metadata: { httpStatusCode: 403 } }); };
  await probeS3ReadWrite(f.input);
  expect(f.result.status).toBe("failed");
  expect(f.result.steps.map(step => step.status)).toEqual(["passed", "passed", "passed", "failed"]);
  expect(f.result.reason).toContain("清理失败");
  expect(f.result.versionId).toBe("v1");
});


for (const status of [403, 302]) test(`presigned HTTP ${status} fails download and still removes the uploaded version`, async () => {
  const f = fixture();
  f.input.download = async () => ({ status, headers: { "x-amz-request-id": "request-1" },
    body: Buffer.from("<Error><Code>SignatureDoesNotMatch</Code><Message>SECRET URL</Message></Error>") });
  await probeS3ReadWrite(f.input);
  expect(f.result.status).toBe("failed");
  expect(f.result.steps[2]!.failure).toMatchObject({ httpStatus: status, code: "SignatureDoesNotMatch", requestId: "request-1" });
  expect(f.result.steps[3]!.status).toBe("passed");
  expect(JSON.stringify(f.result)).not.toContain("SECRET");
});

test("presign failure is separate from download and still cleans up the upload", async () => {
  const f = fixture();
  f.client.presignGetObject = async () => { throw new Error("failed signing SECRET"); };
  await probeS3ReadWrite(f.input);
  expect(f.result.steps.map(step => step.status)).toEqual(["passed", "failed", "not-run", "passed"]);
  expect(f.calls.map(call => call.operation)).toEqual(["put", "delete"]);
  expect(JSON.stringify(f.result)).not.toContain("SECRET");
});

for (const status of [301, 307]) test(`S3 HTTP ${status} evidence retains Location but redacts signing parameters`, async () => {
  const { captureS3HttpResponse } = await import("../src/s3/probe-response");
  const response = captureS3HttpResponse({ status, headers: {
    location: "https://store/key?X-Amz-Signature=signature-secret&X-Amz-Credential=access-secret&X-Amz-Security-Token=session-secret",
  }, body: Buffer.from("") }, []);
  expect(response.httpStatus).toBe(status);
  const location = new URL(response.headers!.location!);
  expect(location.origin + location.pathname).toBe("https://store/key");
  for (const parameter of ["X-Amz-Signature", "X-Amz-Credential", "X-Amz-Security-Token"]) {
    expect(location.searchParams.get(parameter)).toBe("[redacted]");
  }
  for (const secret of ["signature-secret", "access-secret", "session-secret"]) expect(JSON.stringify(response)).not.toContain(secret);
});

test("S3 response evidence retains diagnostics and removes credentials before truncation", async () => {
  const { captureS3HttpResponse, captureS3ErrorResponse } = await import("../src/s3/probe-response");
  const body = '<Error><Code>SignatureDoesNotMatch</Code><Message>wrong region</Message><Region>eu-west-1</Region>'
    + '<CanonicalRequest>GET /private key</CanonicalRequest><AWSAccessKeyId>access-secret</AWSAccessKeyId>'
    + '<SignatureProvided>signature-secret</SignatureProvided><StringToSignBytes>binary-secret</StringToSignBytes>'
    + '<Resource>https://store/key?X-Amz-Signature=signature-secret&amp;X-Amz-Credential=access-secret</Resource></Error>';
  const response = captureS3HttpResponse({ status: 403, headers: { "x-amz-request-id": "req-1", "x-amz-id-2": "host-1",
    "x-amz-bucket-region": "eu-west-1", "set-cookie": "session-secret", "content-type": "application/xml" }, body: Buffer.from(body) },
    ["access-secret", "signature-secret"]);
  expect(response).toMatchObject({ httpStatus: 403, requestId: "req-1", extendedRequestId: "host-1",
    headers: { "x-amz-bucket-region": "eu-west-1" }, bodyTruncated: false });
  expect(response.headers).not.toHaveProperty("set-cookie");
  expect(response.body).toContain("SignatureDoesNotMatch"); expect(response.body).toContain("wrong region");
  for (const secret of ["access-secret", "signature-secret", "binary-secret", "session-secret", "GET /private key"]) {
    expect(JSON.stringify(response)).not.toContain(secret);
  }
  expect(captureS3HttpResponse({ status: 500, headers: {}, body: Buffer.from("x".repeat(5000)) }, [])).toMatchObject({ bodyTruncated: true });
  const sdk = captureS3ErrorResponse(S3Error.from(Object.assign(new Error("Invalid access-secret credentials"), {
    name: "AccessDenied", Region: "eu-west-1", $metadata: { httpStatusCode: 403, requestId: "put-1", extendedRequestId: "put-host", attempts: 1 },
    $response: { headers: { "x-amz-bucket-region": "eu-west-1" } },
  })), ["access-secret"]);
  expect(sdk).toMatchObject({ httpStatus: 403, requestId: "put-1", extendedRequestId: "put-host", attempts: 1 });
  expect(sdk?.body).toContain("Invalid [redacted] credentials");
});


test("pre-cancelled probes do not access storage and observer failures cannot prevent cleanup", async () => {
  const cancelled = fixture(); cancelled.controller.abort();
  const before = await probeS3ReadWrite(cancelled.input);
  expect(before.status).toBe("cancelled"); expect(cancelled.calls).toEqual([]);
  const f = fixture();
  const result = await probeS3ReadWrite({ ...f.input, checkpoint: snapshot => {
    (snapshot as S3ReadWriteProbe).key = "cannot-change-cleanup";
    throw new Error("broken observer");
  } });
  expect(result.status).toBe("passed");
  expect(f.calls.at(-1)?.operation).toBe("delete");
  expect(f.calls.at(-1)?.key).toBe(result.key);
});
