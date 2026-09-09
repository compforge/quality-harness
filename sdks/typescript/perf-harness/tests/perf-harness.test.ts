import { describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadCaseSet } from "@compforge/spec-case/model";
import {
  buildWindows,
  Engine,
  rampHold,
  serializeOutcomes,
  serializeRun,
  writeRunData,
  type Outcome,
  type Workload,
} from "../src";

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
const PERF_FIXTURES = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../conformance/perf/fixtures",
);
const SHARED_CASESET = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../conformance/case/fixtures/shared-chat.yaml",
);

describe("TypeScript perf harness", () => {
  test("closed-loop holds the requested concurrency and records request metrics", async () => {
    let inflight = 0;
    let peak = 0;
    const workload: Workload = {
      fire: async (): Promise<Outcome> => {
        const started = performance.now();
        inflight += 1;
        peak = Math.max(peak, inflight);
        await sleep(15);
        inflight -= 1;
        return {
          status: 200,
          duration_ms: performance.now() - started,
          metrics: { ttft_ms: 5 },
          meta: { trace_id: `trace-${Math.random()}` },
        };
      },
    };
    const run = await new Engine({
      name: "closed",
      service: { name: "chat" },
      workload,
      loads: [rampHold("closed", 5, 0, 0.12, { graceful_stop_s: 1 })],
    }).run();

    expect(peak).toBe(5);
    expect(run.trials).toHaveLength(1);
    expect(run.trials[0]!.windows[0]!.request!.n).toBeGreaterThan(5);
    expect(run.trials[0]!.windows[0]!.request!.metrics.ttft_ms!.p95).toBe(5);
    expect(run.trials[0]!.outcomes.every(({ outcome }) => outcome.meta?.trace_id)).toBe(true);
  });

  test("request limit is an exact safety rail", async () => {
    const workload: Workload = {
      fire: async () => ({ status: 200, duration_ms: 1 }),
    };
    const run = await new Engine({
      service: { name: "chat" },
      workload,
      loads: [rampHold("closed", 5, 0, 1, { max_requests: 12 })],
    }).run();
    expect(run.trials[0]!.stop.reason).toBe("request_limit");
    expect(run.trials[0]!.outcomes).toHaveLength(12);
  });

  test("loads multiple canonical Cases and reduces each Case independently", async () => {
    const originalRandom = Math.random;
    let pick = 0;
    Math.random = () => (pick++ % 2 === 0 ? 0.1 : 0.9);
    try {
      const caseSet = loadCaseSet(SHARED_CASESET);
      const run = await new Engine({
        service: { name: "chat" },
        workload: {
          fire: async ({ case: selected }) => ({
            status: 200,
            duration_ms: selected.id === "ordinary_chat" ? 10 : 20,
          }),
        },
        caseSet,
        caseMix: [{ id: "ordinary_chat", weight: 1 }, { id: "knowledge_chat", weight: 1 }],
        loads: [rampHold("closed", 1, 0, 1, { max_requests: 4 })],
      }).run();

      const byCase = run.trials[0]!.windows[0]!.by_case;
      expect(Object.keys(byCase).sort()).toEqual(["knowledge_chat", "ordinary_chat"]);
      expect(byCase.ordinary_chat!.n + byCase.knowledge_chat!.n).toBe(4);
      expect(byCase.ordinary_chat!.p50_ms).toBe(10);
      expect(byCase.knowledge_chat!.p50_ms).toBe(20);
    } finally {
      Math.random = originalRandom;
    }
  });

  test("rejects experiment-local Case overrides and unknown selections", () => {
    const base = {
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1 }) },
      loads: [rampHold("closed", 1, 0, 1)],
    };
    expect(() => new Engine({ ...base, caseMix: [{ id: "ordinary_chat" }] })).toThrow("caseSet");
    expect(() => new Engine({
      ...base,
      caseSet: loadCaseSet(SHARED_CASESET),
      caseMix: [{ id: "missing" }],
    })).toThrow("not found");
  });

  test("error-rate breaker stops a failing trial", async () => {
    const workload: Workload = {
      fire: async () => ({ status: 503, duration_ms: 1 }),
    };
    const run = await new Engine({
      service: { name: "chat" },
      workload,
      loads: [rampHold("closed", 2, 0, 1, {
        abort_on_error_rate: 0.5,
        breaker_min_n: 4,
      })],
    }).run();
    expect(run.trials[0]!.stop.reason).toBe("error_rate");
    expect(run.passed).toBe(false);
  });

  test("validates shared safety fields before starting load", () => {
    expect(() => new Engine({
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1 }) },
      loads: [rampHold("closed", 1, 0, 1, { max_requests: 0 })],
    })).toThrow("max_requests");
    expect(() => new Engine({
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1 }) },
      loads: [rampHold("closed", 1, 0, 1, { warmup_s: -1 })],
    })).toThrow("warmup_s");
    expect(() => new Engine({
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1 }) },
      loads: [{
        model: "closed",
        schedule: { start_level: Number.NaN, stages: [{ over_s: 1, to_level: 1, kind: "hold" }] },
      }],
    })).toThrow("levels and durations");
    expect(() => new Engine({
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1 }) },
      loads: [rampHold("closed", 1, 0, 1, {
        pacing: { kind: "between", secs: 2, max_secs: 1 },
      })],
    })).toThrow("max_secs");
  });

  test("clips stage drill-downs to the post-warmup measurement interval", () => {
    const load = {
      model: "closed" as const,
      schedule: {
        start_level: 0,
        stages: [
          { over_s: 0.5, to_level: 1, kind: "ramp" as const },
          { over_s: 0.5, to_level: 1, kind: "hold" as const },
        ],
      },
      warmup_s: 0.75,
    };
    const windows = buildWindows(load, [
      { t: 0.25, outcome: { status: 200, duration_ms: 5, ok: true } },
      { t: 0.8, outcome: { status: 200, duration_ms: 10, ok: true } },
    ], 1);

    expect(windows.map((window) => window.id)).toEqual(["measurement", "stage-02"]);
    expect(windows[0]).toMatchObject({ start_s: 0.75, end_s: 1, request: { n: 1 } });
    expect(windows[1]).toMatchObject({ start_s: 0.75, end_s: 1, request: { n: 1 } });
  });

  test("preserves phase errors as diagnostic artifacts and stops the sweep", async () => {
    const engine = new Engine({
      service: { name: "chat" },
      workload: {
        setup: async () => { throw new Error("setup failed"); },
        fire: async () => ({ status: 200, duration_ms: 1 }),
        cleanup: async () => { throw new Error("cleanup failed"); },
      },
      loads: [rampHold("closed", 1, 0, 1), rampHold("closed", 2, 0, 1)],
    });
    const run = await engine.run();
    expect(run.passed).toBe(false);
    expect(run.trials).toHaveLength(1);
    expect(run.trials[0]!.stop.reason).toBe("aborted");
    expect(run.trials[0]!.phase_errors).toEqual([
      { phase: "setup", error_type: "Error", message: "setup failed" },
      { phase: "cleanup", error_type: "Error", message: "cleanup failed" },
    ]);

    const directory = mkdtempSync(join(tmpdir(), "ts-perf-error-"));
    try {
      writeRunData(run, directory);
      expect(JSON.parse(readFileSync(join(directory, "run.json"), "utf8"))).toMatchObject({
        passed: false,
        trials: [{ phase_errors: [{ phase: "setup" }, { phase: "cleanup" }] }],
      });
      expect(JSON.parse(readFileSync(join(directory, "verdict.json"), "utf8"))).toMatchObject({
        status: "error",
      });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  test("external cancellation propagates after cleanup", async () => {
    const controller = new AbortController();
    const reason = new Error("stop run");
    const events: string[] = [];
    const engine = new Engine({
      service: { name: "chat" },
      signal: controller.signal,
      workload: {
        setup: async (context) => {
          events.push("setup");
          controller.abort(reason);
          throw context.signal.reason;
        },
        fire: async () => ({ status: 200, duration_ms: 1 }),
        cleanup: async () => { events.push("cleanup"); },
      },
      loads: [rampHold("closed", 1, 0, 1)],
    });

    let caught: unknown;
    try {
      await engine.run();
    } catch (error) {
      caught = error;
    }
    expect(caught).toBe(reason);
    expect(events).toEqual(["setup", "cleanup"]);
  });

  test("empty raw outcome stream contains no invalid blank record", () => {
    expect(serializeOutcomes({
      schema: 4,
      run_id: "empty",
      experiment: "perf",
      created_at: new Date(0).toISOString(),
      service: "chat",
      passed: false,
      n_trials: 0,
      trials: [],
    })).toBe("");
  });

  test("persists shared schema-4 model and raw outcomes", async () => {
    const run = await new Engine({
      service: { name: "chat" },
      workload: { fire: async () => ({ status: 200, duration_ms: 1, meta: { trace_id: "t1" } }) },
      loads: [rampHold("closed", 1, 0, 0.03, { max_requests: 1 })],
    }, { run_id: "fixed" }).run();
    const document = serializeRun(run);
    expect(document).toMatchObject({ schema: 4, run_id: "fixed", n_trials: 1 });

    const directory = mkdtempSync(join(tmpdir(), "ts-perf-"));
    try {
      writeRunData(run, directory);
      expect(JSON.parse(readFileSync(join(directory, "run.json"), "utf8"))).toMatchObject({ schema: 4 });
      expect(JSON.parse(readFileSync(join(directory, "outcomes.jsonl"), "utf8"))).toMatchObject({
        trial: run.trials[0]!.id,
        meta: { trace_id: "t1" },
      });
      expect(JSON.parse(readFileSync(join(directory, "verdict.json"), "utf8"))).toMatchObject({
        harness: "perf",
        scope: "perf",
        run_id: "fixed",
        status: "skipped",
      });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  test("reads the language-neutral conformance fixture", () => {
    const run = JSON.parse(readFileSync(join(PERF_FIXTURES, "basic.run.json"), "utf8"));
    const outcome = JSON.parse(readFileSync(join(PERF_FIXTURES, "basic.outcomes.jsonl"), "utf8"));

    expect(run).toMatchObject({
      schema: 4,
      trials: [{
        id: "default__closed-5c",
        arm: { id: "default__closed-5c" },
        windows: [{
          request: { metrics: { first_token_ms: { p95: 6500 } } },
          by_case: { ordinary_chat: { n: 1 } },
        }],
      }],
    });
    expect(outcome).toMatchObject({
      trial: "default__closed-5c",
      metrics: { first_token_ms: 6500 },
      meta: { trace_id: "0123456789abcdef0123456789abcdef" },
    });
  });
});
