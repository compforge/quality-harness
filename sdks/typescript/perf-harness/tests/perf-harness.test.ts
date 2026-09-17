import { describe, test, expect } from "bun:test";
import {
  mkdtempSync,
  cpSync,
  rmSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Engine } from "../src/engine";
import { validateLoadPlan, target, arrivalTime } from "../src/load";
import { loadRun, writeRunData, serializeRequests } from "../src/runio";
import { requestStats } from "../src/reduce";
import type { Service, Outcome } from "../src/model";
import type { Runner } from "../src/runner";
const service: Service = {
  name: "mock",
  component: {
    name: "api",
    repository: { forge: { name: "github" }, path: "example/mock" },
  },
  environment: { name: "local" },
  workloads: [],
};
function slow(ms: number): Runner & { active: number; peak: number } {
  return {
    name: "mock",
    active: 0,
    peak: 0,
    async fire(ctx) {
      this.active++;
      this.peak = Math.max(this.peak, this.active);
      try {
        await new Promise<void>((resolve, reject) => {
          const finish = () => {
            ctx.signal.removeEventListener("abort", cancel);
            resolve();
          };
          const timer = setTimeout(finish, ms);
          const cancel = () => {
            clearTimeout(timer);
            reject(new Error("cancelled"));
          };
          ctx.signal.addEventListener("abort", cancel, { once: true });
          if (ctx.signal.aborted) cancel();
        });
      } finally {
        this.active--;
      }
      return { status: 200, duration_ms: ms };
    },
  };
}

test("finite rate caps complete request lifetimes and records drops without calls", async () => {
  const runner = slow(80),
    run = await new Engine({
      service,
      runner,
      loads: [
        {
          request_rate: 1000,
          max_concurrency: 3,
          duration_s: 0.06,
          drain_timeout_s: 0.2,
        },
      ],
    }).run();
  const arm = run.executions[0]!;
  expect(runner.peak).toBe(3);
  expect(runner.active).toBe(0);
  expect(arm.operation_runs.length).toBe(3);
  expect(arm.requests.some((r) => r.state === "dropped")).toBe(true);
  expect(
    arm.requests.every(
      (r) => r.dispatched_at === undefined || r.dispatched_at < 0.06,
    ),
  ).toBe(true);
  expect(arm.windows[0]!.request!.completed).toBe(0);
  expect(arm.windows[0]!.request!.n).toBe(3);
  expect(arm.requests.filter((r) => r.state !== "dropped").length).toBe(
    arm.operation_runs.length,
  );
});

test("infinite rate replenishes, downscale does not cancel running SSE", async () => {
  const runner = slow(80),
    run = await new Engine({
      service,
      runner,
      loads: [
        {
          request_rate: Infinity,
          max_concurrency: 4,
          duration_s: 0.12,
          drain_timeout_s: 0.2,
          stages: [
            {
              duration_s: 0.02,
              request_rate: Infinity,
              max_concurrency: 4,
              kind: "hold",
              name: "same",
            },
            {
              duration_s: 0.1,
              request_rate: Infinity,
              max_concurrency: 1,
              kind: "hold",
              name: "same",
            },
          ],
        },
      ],
    }).run();
  const arm = run.executions[0]!;
  expect(runner.peak).toBe(4);
  expect(arm.stop.interrupted).toBe(0);
  expect(
    arm.requests.filter(
      (r) => r.dispatched_at! >= 0.02 && r.dispatched_at! < 0.075,
    ).length,
  ).toBe(0);
  expect(arm.windows.filter((w) => w.kind === "hold").map((w) => w.id)).toEqual(
    ["stage-0", "stage-1"],
  );
  expect(arm.windows.find((w) => w.id === "stage-0")!.request!.n).toBe(4);
  expect(arm.windows.find((w) => w.id === "stage-0")!.request!.completed).toBe(
    0,
  );
});

test("hard stop records every interrupted call and leaves no tasks", async () => {
  const runner = slow(10000),
    run = await new Engine({
      service,
      runner,
      loads: [
        {
          request_rate: Infinity,
          max_concurrency: 4,
          duration_s: 0.03,
          drain_timeout_s: 0,
        },
      ],
      judge: () => {
        throw new Error("must not judge interruption");
      },
    }).run();
  const arm = run.executions[0]!;
  expect(run.passed).toBe(false);
  expect(runner.active).toBe(0);
  expect(arm.stop.interrupted).toBe(4);
  expect(arm.operation_runs.length).toBe(4);
  expect(arm.requests.every((r) => r.state === "interrupted")).toBe(true);
  expect(Object.keys(arm.evaluations).length).toBe(0);
});

test("artifact round trip and rejudge do not change raw evidence", async () => {
  const run = await new Engine({
    service,
    runner: slow(5),
    loads: [{ request_rate: 50, max_concurrency: 2, duration_s: 0.04 }],
  }).run();
  const dir = mkdtempSync(join(tmpdir(), "perf-ts-"));
  try {
    writeRunData(run, dir);
    const loaded = loadRun(dir);
    expect(loaded.executions[0]!.requests).toEqual(run.executions[0]!.requests);
    const arm = loaded.executions[0]!,
      before = serializeRequests(loaded);
    for (const op of arm.operation_runs)
      arm.evaluations[op.id] = { ok: false, error_kind: "business" };
    expect(requestStats(arm, 0, 0.04).error_rate).toBe(1);
    expect(serializeRequests(loaded)).toBe(before);
  } finally {
    rmSync(dir, { recursive: true });
  }
});

test("ramp arrival clock and validation use explicit units", () => {
  const load = {
    request_rate: 0,
    max_concurrency: 2,
    duration_s: 3,
    stages: [
      {
        duration_s: 2,
        request_rate: 20,
        max_concurrency: 4,
        kind: "ramp" as const,
      },
      {
        duration_s: 1,
        request_rate: 20,
        max_concurrency: 4,
        kind: "hold" as const,
      },
    ],
  };
  validateLoadPlan(load);
  expect(target(load, 1)).toEqual([10, 3]);
  expect(arrivalTime(load, 5)).toBeCloseTo(1);
  expect(arrivalTime(load, 30)).toBeCloseTo(2.5);
  for (const max_concurrency of [0, 1.5])
    expect(() => validateLoadPlan({ ...load, max_concurrency })).toThrow();
  expect(() => validateLoadPlan({ ...load, request_rate: Infinity })).toThrow();
});

test("setup failure still cleans up and preserves phase evidence", async () => {
  let cleaned = false;
  const runner: Runner = {
    name: "broken",
    async setup() {
      throw new Error("setup failed");
    },
    async fire() {
      throw new Error("must not execute");
    },
    async cleanup() {
      cleaned = true;
    },
  };
  const run = await new Engine({
    service,
    runner,
    loads: [{ request_rate: 1, max_concurrency: 1, duration_s: 0.01 }],
  }).run();
  expect(cleaned).toBe(true);
  expect(run.passed).toBe(false);
  expect(run.executions[0]!.phase_errors[0]!.phase).toBe("setup");
  expect(run.executions[0]!.operation_runs.length).toBe(0);
});

test("breaker uses completed evaluations and preserves Outcomes", async () => {
  const runner: Runner = {
    name: "fail",
    async fire() {
      return { status: 500, duration_ms: 1 };
    },
  };
  const run = await new Engine({
    service,
    runner,
    loads: [
      {
        request_rate: Infinity,
        max_concurrency: 2,
        duration_s: 1,
        abort_on_error_rate: 0.5,
        breaker_min_n: 4,
      },
    ],
  }).run();
  const arm = run.executions[0]!;
  expect(arm.stop.reason).toBe("error_rate");
  expect(arm.stop.snapshot!.completed).toBeGreaterThanOrEqual(4);
  expect(arm.operation_runs.every((o) => !("ok" in o.outcome))).toBe(true);
});

test("common conformance fixture preserves identity and raw trace correlation", () => {
  const fixture = new URL(
    "../../../../conformance/perf/fixtures/",
    import.meta.url,
  );
  const dir = mkdtempSync(join(tmpdir(), "perf-fixture-"));
  try {
    for (const name of ["run.json", "requests.jsonl", "evaluations.json"])
      cpSync(new URL(`basic.${name}`, fixture), join(dir, name));
    const run = loadRun(dir),
      arm = run.executions[0]!;
    expect(arm.operation_runs[0]!.outcome.meta!.trace_id).toBe(
      "0123456789abcdef0123456789abcdef",
    );
    expect(arm.requests[1]!.state).toBe("dropped");
    expect(arm.arm.load.request_rate).toBe(Infinity);
    for (const window of arm.windows) {
      const actual = requestStats(arm, window.start_s, window.end_s);
      const expected = structuredClone(window.request!);
      actual.caveats.sort();
      expected.caveats.sort();
      for (const metric of Object.values(actual.metrics)) metric.caveats.sort();
      for (const metric of Object.values(expected.metrics))
        metric.caveats.sort();
      expect(actual).toEqual(expected);
    }
  } finally {
    rmSync(dir, { recursive: true });
  }
});

test("external abort interrupts drain promptly and cleans up all calls", async () => {
  const controller = new AbortController();
  const runner = slow(10000);
  const timer = setTimeout(() => controller.abort(), 50);
  const started = performance.now();
  try {
    const run = await new Engine({
      service,
      runner,
      signal: controller.signal,
      loads: [
        {
          request_rate: Infinity,
          max_concurrency: 2,
          duration_s: 0.01,
          drain_timeout_s: 10,
        },
      ],
    }).run();
    expect(performance.now() - started).toBeLessThan(1000);
    expect(run.executions[0]!.stop.reason).toBe("aborted");
    expect(run.executions[0]!.stop.interrupted).toBe(2);
    expect(runner.active).toBe(0);
  } finally {
    clearTimeout(timer);
  }
});

test("saturated immediate Runner yields to cancellation without polling-limited throughput", async () => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30);
  const started = performance.now();
  try {
    const run = await new Engine({
      service,
      signal: controller.signal,
      runner: {
        name: "immediate",
        async fire() {
          return { status: 200, duration_ms: 0 };
        },
      },
      loads: [{ request_rate: Infinity, max_concurrency: 1, duration_s: 2 }],
    }).run();
    expect(performance.now() - started).toBeLessThan(1000);
    expect(run.executions[0]!.operation_runs.length).toBeGreaterThan(5);
    expect(run.executions[0]!.stop.reason).toBe("aborted");
  } finally {
    clearTimeout(timer);
  }
});

const failureScenarios = JSON.parse(
  readFileSync(
    new URL(
      "../../../../conformance/perf/fixtures/judge-failure.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  name: string;
  duration_s: number;
  first_response_s: number;
  cancellation_delay_s: number;
  drain_timeout_s: number;
  stop_reason: string;
  measurement_complete: boolean;
  inflight_at_stop: number;
  completed: number;
}[];

for (const scenario of failureScenarios) {
  test(`${scenario.name} preserves boundaries, raw outcomes and cancellation census`, async () => {
    let calls = 0,
      active = 0,
      cleaned = false;
    const runner: Runner = {
      name: "slow-cancel",
      async fire(ctx) {
        const first = ++calls === 1;
        active++;
        try {
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(
              () => {
                ctx.signal.removeEventListener("abort", cancel);
                resolve();
              },
              first ? scenario.first_response_s * 1000 : 10000,
            );
            const cancel = () => {
              clearTimeout(timer);
              setTimeout(
                () => reject(new Error("cancelled")),
                scenario.cancellation_delay_s * 1000,
              );
            };
            ctx.signal.addEventListener("abort", cancel, { once: true });
          });
        } finally {
          active--;
        }
        return {
          status: 200,
          duration_ms: scenario.first_response_s * 1000,
          meta: { trace_id: "completed-before-judge-error" },
        };
      },
      async cleanup() {
        expect(active).toBe(0);
        cleaned = true;
      },
    };
    const run = await new Engine({
      service,
      runner,
      judge: () => {
        throw new Error("judge unavailable");
      },
      loads: [
        {
          request_rate: Infinity,
          max_concurrency: 2,
          duration_s: scenario.duration_s,
          drain_timeout_s: scenario.drain_timeout_s,
        },
      ],
    }).run();
    const arm = run.executions[0]!,
      measurement = arm.windows[0]!;
    expect(run.passed).toBe(false);
    expect(cleaned).toBe(true);
    expect(arm.phase_errors[0]!.message).toBe("judge unavailable");
    expect(arm.stop.reason).toBe(scenario.stop_reason);
    expect(arm.stop.inflight_at_stop).toBe(scenario.inflight_at_stop);
    expect(arm.stop.interrupted).toBe(1);
    expect(arm.stop.force_cancelled).toBe(true);
    expect(measurement.complete).toBe(scenario.measurement_complete);
    expect(measurement.end_s).toBeGreaterThan(0);
    if (scenario.measurement_complete)
      expect(measurement.end_s).toBe(scenario.duration_s);
    else expect(measurement.end_s).toBeLessThan(scenario.duration_s);
    expect(measurement.request!.completed).toBe(scenario.completed);
    expect(measurement.request!.n).toBe(1);
    expect(measurement.request!.n_interrupted).toBe(1);
    expect(measurement.request!.error_breakdown).toEqual({ unjudged: 1 });
    expect(arm.evaluations).toEqual({});
    const interrupted = arm.requests.find((r) => r.state === "interrupted")!;
    expect(interrupted.finished_at!).toBeGreaterThan(measurement.end_s);
    const drain = arm.windows.find((w) => w.kind === "drain")!;
    expect(drain.start_s).toBe(measurement.end_s);
    expect(drain.request!.completed).toBe(1 - scenario.completed);
    expect(
      arm.operation_runs.some(
        (o) => o.outcome.meta?.trace_id === "completed-before-judge-error",
      ),
    ).toBe(true);
    const dir = mkdtempSync(join(tmpdir(), "perf-judge-error-"));
    try {
      writeRunData(run, dir);
      const restored = loadRun(dir).executions[0]!;
      expect(restored.requests).toEqual(arm.requests);
      expect(restored.stop).toEqual(arm.stop);
      expect(restored.windows).toEqual(arm.windows);
    } finally {
      rmSync(dir, { recursive: true });
    }
  });
}

test("duplicate Arms are rejected before setup or requests", async () => {
  let setups = 0;
  const runner: Runner = {
    name: "never",
    async setup() {
      setups++;
    },
    async fire() {
      throw new Error("must not fire");
    },
  };
  const load = { request_rate: 50, max_concurrency: 2, duration_s: 0.04 };
  for (const config of [
    { loads: [load, { ...load }] },
    {
      loads: [
        load,
        {
          duration_s: 0.04,
          max_concurrency: 2,
          request_rate: 50,
          arrival: "constant" as const,
        },
      ],
    },
    {
      loads: [load],
      resources: [
        { extra: { a: "1", b: "2" } },
        { replicas: 1, extra: { b: "2", a: "1" } },
      ],
    },
    { loads: [load], resources: [{ workers: 2 }, { workers: 2 }] },
  ]) {
    await expect(
      new Engine({ service, runner, ...config }).run(),
    ).rejects.toThrow("duplicate arm id");
  }
  expect(setups).toBe(0);
});

test("distinct configurations sharing a display label retain separate evidence after reload", async () => {
  const run = await new Engine({
    service,
    runner: slow(1),
    loads: [
      { request_rate: 50, max_concurrency: 2, duration_s: 0.03 },
      { request_rate: 50, max_concurrency: 2, duration_s: 0.035 },
    ],
  }).run();
  const dir = mkdtempSync(join(tmpdir(), "perf-identities-"));
  try {
    expect(new Set(run.executions.map((e) => e.id)).size).toBe(2);
    expect(run.executions.map((e) => e.requests.length)).toEqual([2, 2]);
    writeRunData(run, dir);
    const restored = loadRun(dir);
    expect(restored.executions.map((e) => e.requests)).toEqual(
      run.executions.map((e) => e.requests),
    );
    expect(restored.executions.map((e) => e.evaluations)).toEqual(
      run.executions.map((e) => e.evaluations),
    );
    const model = JSON.parse(readFileSync(join(dir, "run.json"), "utf8"));
    model.executions[1].id = model.executions[0].id;
    writeFileSync(join(dir, "run.json"), JSON.stringify(model));
    expect(() => loadRun(dir)).toThrow("duplicate ArmRun id");
  } finally {
    rmSync(dir, { recursive: true });
  }
});
