import { expect, test } from "bun:test";
import { Engine } from "../src/engine";
import type { Runner } from "../src/runner";

const service = { name: "cancel-test" };
const load = {
  request_rate: Infinity,
  max_inflight: 1,
  warmup: { step_s: 0 },
  hold_s: 0.01,
  cooldown_timeout_s: 10,
};

test("cancellation immediately reaches requests with its original reason and preserves evidence", async () => {
  const controller = new AbortController();
  const reason = new Error("host cancelled the experiment");
  let notifiedImmediately = false;
  let requestReason: unknown;
  let calls = 0;
  let cleaned = false;
  const run = await new Engine({
    service,
    signal: controller.signal,
    runner: {
      name: "cancel-aware",
      async fire({ signal }) {
        calls++;
        return new Promise<never>((_, reject) => {
          signal.addEventListener("abort", () => {
            requestReason = signal.reason;
            reject(signal.reason);
          }, { once: true });
          controller.abort(reason);
          notifiedImmediately = signal.aborted;
        });
      },
      async cleanup({ signal }) {
        expect(signal.reason).toBe(reason);
        cleaned = true;
      },
    },
    judge() { throw new Error("interrupted requests must not be judged"); },
    loads: [load, { ...load, max_inflight: 2 }],
  }).run();

  expect(notifiedImmediately).toBe(true);
  expect(requestReason).toBe(reason);
  expect(cleaned).toBe(true);
  expect(calls).toBe(1);
  expect(run.passed).toBe(false);
  expect(run.executions).toHaveLength(1);
  expect(run.executions[0]!.stop).toMatchObject({ reason: "aborted", inflight_at_stop: 1, interrupted: 1 });
  expect(run.executions[0]!.requests[0]!.state).toBe("interrupted");
  expect(run.executions[0]!.operation_runs).toHaveLength(1);
  expect(run.executions[0]!.evaluations).toEqual({});
});

test.each(["setup", "onArmStart"] as const)("cancellation during %s prevents load and still runs cleanup", async (phase) => {
  const controller = new AbortController();
  const reason = new Error(`cancel during ${phase}`);
  const events: string[] = [];
  const cancel = () => controller.abort(reason);
  const runner: Runner = {
    name: "cancel-before-load",
    async setup() {
      events.push("setup");
      if (phase === "setup") cancel();
    },
    async fire() { throw new Error("must not send requests after cancellation"); },
    async cleanup({ signal }) {
      expect(signal.reason).toBe(reason);
      events.push("cleanup");
    },
  };
  await expect(new Engine({
    service, runner, signal: controller.signal, loads: [load],
    onArmStart() {
      events.push("start");
      if (phase === "onArmStart") cancel();
    },
  }).run()).rejects.toBe(reason);
  expect(events).toEqual(phase === "setup" ? ["setup", "cleanup"] : ["setup", "start", "cleanup"]);
});

test("cleanup still receives cancellation and cannot report a passed run", async () => {
  const controller = new AbortController();
  const reason = new Error("cancel during cleanup");
  let cleanupReason: unknown;
  const run = await new Engine({
    service, signal: controller.signal, loads: [load],
    runner: {
      name: "cancel-during-cleanup",
      async fire() { return { status: 200, duration_ms: 0 }; },
      async cleanup({ signal }) {
        controller.abort(reason);
        cleanupReason = signal.reason;
      },
    },
  }).run();
  expect(cleanupReason).toBe(reason);
  expect(run.passed).toBe(false);
  // Cancellation after measurement does not rewrite its already recorded boundary.
  expect(run.executions[0]!.stop.reason).toBe("deadline");
});

test("an already cancelled experiment does not acquire runner resources", async () => {
  const controller = new AbortController();
  controller.abort(new Error("cancelled before start"));
  const run = await new Engine({
    service, signal: controller.signal, loads: [load],
    runner: {
      name: "never-start",
      async setup() { throw new Error("must not acquire resources"); },
      async fire() { throw new Error("must not send requests"); },
    },
  }).run();
  expect(run.passed).toBe(false);
  expect(run.executions).toEqual([]);
});
