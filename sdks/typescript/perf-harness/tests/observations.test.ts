import { expect, test } from "bun:test";
import { readFileSync, mkdtempSync, copyFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadRun, serializeRun, serializeRequests, writeRunData } from "../src/runio";
import { requestStats } from "../src/reduce";
import { defaultJudge } from "../src/judge";
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
      if (item.result) arm.evaluations[item.id] = {result: item.result as RequestEvaluation["result"]};
    }
    for (const window of fixture.windows) {
      const stats = requestStats(arm, window.start, window.end);
      for (const [key, value] of Object.entries(window.counts))
        expect(stats[key as keyof typeof stats]).toBe(value as number);
      expect(stats.throughput_rps).toBeCloseTo(stats.success_rps + stats.failure_rps +
        stats.rejection_rps + stats.timeout_rps + stats.unjudged_rps);
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

test("default transport classification distinguishes explicit rejection and timeout", () => {
  for (const [status, result] of [[200,"success"], [429,"rejected"], [503,"failure"], [504,"timeout"]] as const)
    expect(defaultJudge({status, duration_ms: 1}).result).toBe(result);
  expect(defaultJudge({status: null, duration_ms: 1, meta: {exc: "ReadTimeout"}}).result).toBe("timeout");
});
