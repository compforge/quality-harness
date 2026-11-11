import { expect, test } from "bun:test";
import { readFileSync, mkdtempSync, copyFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadRun, serializeRun, serializeRequests, writeRunData } from "../src/runio";
import { requestStats } from "../src/reduce";
import { defaultJudge, validateEvaluation } from "../src/judge";
import type { RequestEvaluation } from "../src/model";

const fixtures = new URL("../../../../conformance/perf/fixtures/", import.meta.url);
const fixture = JSON.parse(readFileSync(new URL("throughput.json", fixtures), "utf8"));

test("completion classes partition event windows and remain independently rejudgable", () => {
  const directory = mkdtempSync(join(tmpdir(), "perf-results-"));
  try {
    for (const [source, target] of [["basic.run.json", "run.json"],
      ["basic.requests.jsonl", "requests.jsonl"], ["basic.evaluations.json", "evaluations.json"]])
      copyFileSync(new URL(source!, fixtures), join(directory, target!));
    const run = loadRun(directory), arm = run.executions[0]!;
    const originalCall = arm.operation_runs[0]!;
    arm.requests = []; arm.operation_runs = []; arm.evaluations = {};
    for (const item of fixture.requests) {
      const state = item.state ?? "finished";
      arm.requests.push({id: item.id, case_id: "case", scheduled_at: item.start,
        arrived_at: item.start, state, facets: {kind: "chat"},
        ...(state === "dropped" ? {} : {dispatched_at: item.start,
          finished_at: item.end, operation_run_id: item.id})});
      if (state !== "dropped") arm.operation_runs.push({...originalCall, id: item.id,
        outcome: {status: 200, duration_ms: (item.end-item.start)*1000}});
      if (item.code) arm.evaluations[item.id] = {code: item.code as RequestEvaluation["code"]};
    }
    for (const window of fixture.windows) {
      const stats = requestStats(arm, window.start, window.end);
      for (const [key, value] of Object.entries(window.counts))
        expect(stats[key as keyof typeof stats]).toEqual(value);
      expect(stats.throughput_rps).toBeCloseTo(Object.values(stats.throughput_by_code).reduce((sum, rate) => sum + rate, 0) + stats.unjudged_rps);
    }
    const raw = serializeRequests(run);
    arm.evaluations = {};
    const unknown = requestStats(arm, 0, 10);
    expect(unknown.unjudged).toBe(5);
    expect(unknown.n_judged).toBe(0);
    expect(unknown.error_breakdown).toEqual({});
    writeRunData(run, directory);
    expect(serializeRequests(loadRun(directory))).toBe(raw);
    expect(serializeRun(run)).not.toHaveProperty("passed");
    expect(serializeRun(run).schema).toBe(7);
  } finally { rmSync(directory, { recursive: true }); }
});

test("default Judge preserves HTTP error codes and transport failures", () => {
  for (const [status, result] of [[200,"success"], [429,"http_429"], [503,"http_503"], [504,"http_504"]] as const)
    expect(defaultJudge({status, duration_ms: 1}).code).toBe(result);
  expect(defaultJudge({status: null, duration_ms: 1, meta: {exc: "ReadTimeout"}}).code).toBe("timeout");
});


test("open codes preserve prototype-like names and distinguish missing judgments", () => {
  for (const code of ["model_unavailable", "__proto__", "unjudged"]) {
    validateEvaluation({code});
    const directory = mkdtempSync(join(tmpdir(), "perf-code-"));
    try {
      for (const [source, target] of [["basic.run.json", "run.json"],
        ["basic.requests.jsonl", "requests.jsonl"], ["basic.evaluations.json", "evaluations.json"]])
        copyFileSync(new URL(source!, fixtures), join(directory, target!));
      const run = loadRun(directory), arm = run.executions[0]!;
      for (const id of Object.keys(arm.evaluations)) arm.evaluations[id] = {code};
      const stats = requestStats(arm, 0, 10);
      expect(stats.completed_by_code).toEqual({[code]: stats.completed});
      expect(stats.throughput_by_code).toEqual({[code]: stats.throughput_rps});
      expect(stats.unjudged).toBe(0);
      expect(stats.succeeded).toBe(0);
      expect(stats.error_breakdown).toEqual({[code]: stats.n_judged});
      arm.windows[0]!.request = stats;
      writeRunData(run, directory);
      expect(loadRun(directory).executions[0]!.windows[0]!.request).toEqual(stats);
    } finally { rmSync(directory, {recursive: true}); }
  }
  for (const code of ["", "   ", null, 0])
    expect(() => validateEvaluation({code})).toThrow("nonempty string");
});
