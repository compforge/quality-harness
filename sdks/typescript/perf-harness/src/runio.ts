import { Graph, Operation, HttpOperation, SSEOperation } from "@compforge/harness-common";
import { validateEvaluation } from "./judge";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Run, ArmRun } from "./model";
import { loadSeries, serializeSeries } from "./series";
import { serializeLoadPlan } from "./load";

export function serializeRun(run: Run): Record<string, unknown> {
  return {
    schema: 12,
    run_id: run.run_id,
    experiment: run.experiment,
    created_at: run.created_at,
    service: run.service,
    status: run.status,
    environment: run.environment,
    ...(run.graph ? {graph: run.graph} : {}),
    ...(run.report_columns?.length ? {report_columns: run.report_columns} : {}),
    case_entries: run.case_entries,
    case_ids: run.case_ids ?? [],
    entry_ids: run.entry_ids ?? [],
    observation_bindings: run.observation_bindings ?? [],
    executions: run.executions.map((e) => ({
      id: e.id,
      started_at: e.started_at,
      finished_at: e.finished_at,
      started_at_s: e.started_at_s ?? null,
      prometheus_observations: e.prometheus_observations ?? [],
      service: e.service,
      arm: {
        id: e.arm.id,
        resources: e.arm.resources,
        load: serializeLoadPlan(e.arm.load),
      },
      windows: e.windows,
      phases: e.phases,
      stop: e.stop,
      registry: e.registry,
      probe_errors: e.probe_errors,
      phase_errors: e.phase_errors,
    })),
  };
}
export function serializeRequests(run: Run): string {
  const lines: string[] = [];
  for (const e of run.executions) {
    const calls = new Map(e.operation_runs.map((o) => [o.id, o]));
    for (const record of e.requests) {
      const call = record.operation_run_id
        ? calls.get(record.operation_run_id)
        : undefined;
      const operation_run = call
        ? {
            ...call,
            // Static topology/probes belong to the Run, not every request record.
            operation: Object.fromEntries(Object.entries(call.operation).filter(([key]) => ["name", "method", "path", "response_mode"].includes(key))),
            service: {
              name: call.service.name,
              component: call.service.component,
              environment: { name: call.service.environment.name },
              workloads: call.service.workloads ?? [],
            },
            outcome: {
              events: 0,
              nbytes: 0,
              metrics: {},
              meta: {},
              facets: {},
              case_id: record.case_id,
              ...call.outcome,
            },
          }
        : undefined;
      lines.push(
        JSON.stringify({ arm_run_id: e.id, request: record, operation_run }),
      );
    }
  }
  return lines.length ? lines.join("\n") + "\n" : "";
}
export function serializeVerdict(run: Run): Record<string, unknown> {
  const error = run.executions.find((e) => e.phase_errors.length),
    failed = run.executions.find(
      (e) => !["deadline", "manual"].includes(e.stop.reason) || e.stop.interrupted,
    );
  return {
    schema_version: 1,
    harness: "perf",
    scope: run.experiment,
    run_id: run.run_id,
    status: error ? "error" : "skipped",
    ...(error
      ? { reason: `execution ${error.id}: ${error.phase_errors[0]!.message}` }
      : failed
        ? {
            reason: `execution ${failed.id}: ${failed.stop.reason}, interrupted=${failed.stop.interrupted}`,
          }
        : {}),
    artifact_paths: {
      model: "run.json",
      requests: "requests.jsonl",
      evaluations: "evaluations.json",
    },
    created_at: run.created_at,
  };
}
export function writeRunData(run: Run, directory: string) {
  mkdirSync(directory, { recursive: true });
  const paths = {
    run_json: join(directory, "run.json"),
    requests: join(directory, "requests.jsonl"),
    evaluations: join(directory, "evaluations.json"),
    verdict: join(directory, "verdict.json"),
  };
  writeFileSync(paths.run_json, JSON.stringify(serializeRun(run), null, 2));
  writeFileSync(paths.requests, serializeRequests(run));
  writeFileSync(
    paths.evaluations,
    JSON.stringify(
      Object.fromEntries(run.executions.map((e) => [e.id, e.evaluations])),
      null,
      2,
    ),
  );
  writeFileSync(paths.verdict, JSON.stringify(serializeVerdict(run), null, 2));
  writeFileSync(join(directory, "timeseries.csv"), serializeSeries(run.executions));
  return paths;
}
/** Load facts and evaluations without contacting the tested service. */
export function loadRun(directory: string): Run {
  const data = JSON.parse(readFileSync(join(directory, "run.json"), "utf8"));
  if (data.schema !== 12)
    throw new Error(`unsupported perf schema ${data.schema}`);
  if (data.graph) {
    const graph = Graph.fromJSON(data.graph);
    if (graph.environment !== data.environment) throw new Error("Graph environment must match Run");
    data.graph = graph.toJSON();
  }
  const evaluations = JSON.parse(
    readFileSync(join(directory, "evaluations.json"), "utf8"),
  );
  for (const values of Object.values(evaluations))
    for (const value of Object.values(values as Record<string, unknown>)) validateEvaluation(value);
  const executions: ArmRun[] = data.executions.map((e: ArmRun) => ({
    ...e,
    arm: {
      ...e.arm,
      load: {
        ...e.arm.load,
        request_rate:
          String(e.arm.load.request_rate) === "inf"
            ? Infinity
            : Number(e.arm.load.request_rate),
        stages: (e.arm.load.stages ?? []).map((s) => ({
          ...s,
          request_rate:
            String(s.request_rate) === "inf"
              ? Infinity
              : Number(s.request_rate),
        })),
      },
    },
    operation_runs: [],
    requests: [],
    evaluations: evaluations[e.id],
  }));
  const byId = new Map(executions.map((e) => [e.id, e]));
  if (byId.size !== executions.length)
    throw new Error(
      "duplicate ArmRun id in run.json; request ownership is ambiguous",
    );
  loadSeries(join(directory, "timeseries.csv"), byId);
  for (const line of readFileSync(join(directory, "requests.jsonl"), "utf8")
    .split("\n")
    .filter(Boolean)) {
    const row = JSON.parse(line),
      e = byId.get(row.arm_run_id)!;
    const record = row.request;
    for (const key of [
      "dispatched_at",
      "finished_at",
      "reason",
      "operation_run_id",
    ]) {
      if (record[key] === null) delete record[key];
    }
    e.requests.push(record);
    if (row.operation_run) {
      const call = row.operation_run, declaration = call.operation;
      const value = {...declaration, serviceName: call.service.name};
      if (declaration.method !== undefined) {
        if (!["unary","sse"].includes(declaration.response_mode)) throw new Error("Unsupported HTTP response mode");
        call.operation = declaration.response_mode === "sse" ? new SSEOperation(value) : new HttpOperation(value);
      } else call.operation = new Operation(value);
      e.operation_runs.push(call);
    }
  }
  return {
    ...data,
    executions,
    artifacts: [
      { name: "model", path: "run.json" },
      { name: "requests", path: "requests.jsonl" },
      { name: "evaluations", path: "evaluations.json" },
    ],
  };
}
