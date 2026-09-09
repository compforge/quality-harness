export type LoadModel = "open" | "closed";
export type StageKind = "ramp" | "hold";

export interface Stage {
  over_s: number;
  to_level: number;
  kind: StageKind;
  name?: string;
}

export interface Schedule {
  start_level: number;
  stages: Stage[];
}

export type Pacing =
  | { kind: "none" }
  | { kind: "constant"; secs: number }
  | { kind: "between"; secs: number; max_secs: number }
  | { kind: "constant_pacing"; secs: number };

export interface LoadProfile {
  model: LoadModel;
  schedule: Schedule;
  pacing?: Pacing;
  warmup_s?: number;
  max_inflight?: number;
  max_requests?: number;
  abort_on_error_rate?: number;
  breaker_min_n?: number;
  graceful_stop_s?: number;
}

export function validateLoadProfile(load: LoadProfile): void {
  if (!load.schedule.stages.length) throw new Error("load.schedule requires at least one stage");
  if (!Number.isFinite(load.schedule.start_level) || load.schedule.start_level < 0
    || load.schedule.stages.some(
    (stage) => !Number.isFinite(stage.over_s) || stage.over_s < 0
      || !Number.isFinite(stage.to_level) || stage.to_level < 0,
  )) throw new Error("load schedule levels and durations must be finite and >= 0");
  for (const [name, value] of [
    ["max_inflight", load.max_inflight],
    ["max_requests", load.max_requests],
    ["breaker_min_n", load.breaker_min_n],
  ] as const) {
    if (value !== undefined && (!Number.isInteger(value) || value < 1)) {
      throw new Error(`load.${name} must be an integer >= 1`);
    }
  }
  if (load.abort_on_error_rate !== undefined
    && (!Number.isFinite(load.abort_on_error_rate)
      || load.abort_on_error_rate <= 0
      || load.abort_on_error_rate > 1)) {
    throw new Error("load.abort_on_error_rate must be in (0, 1]");
  }
  const pacing = load.pacing;
  if (pacing && pacing.kind !== "none") {
    if (!Number.isFinite(pacing.secs) || pacing.secs < 0) {
      throw new Error("load.pacing.secs must be finite and >= 0");
    }
    if (pacing.kind === "between"
      && (!Number.isFinite(pacing.max_secs) || pacing.max_secs < pacing.secs)) {
      throw new Error("load.pacing.max_secs must be finite and >= pacing.secs");
    }
  }
  for (const [name, value] of [
    ["warmup_s", load.warmup_s],
    ["graceful_stop_s", load.graceful_stop_s],
  ] as const) {
    if (value !== undefined && (!Number.isFinite(value) || value < 0)) {
      throw new Error(`load.${name} must be finite and >= 0`);
    }
  }
}

export function scheduleDuration(schedule: Schedule): number {
  return schedule.stages.reduce((sum, stage) => sum + stage.over_s, 0);
}

export function peakLevel(schedule: Schedule): number {
  return Math.max(schedule.start_level, ...schedule.stages.map((stage) => stage.to_level));
}

export function intensityAt(schedule: Schedule, elapsedS: number): number {
  let level = schedule.start_level;
  let clock = 0;
  for (const stage of schedule.stages) {
    if (elapsedS < clock + stage.over_s) {
      if (stage.kind === "hold" || stage.over_s <= 0) return stage.to_level;
      return level + (stage.to_level - level) * (Math.max(elapsedS - clock, 0) / stage.over_s);
    }
    clock += stage.over_s;
    level = stage.to_level;
  }
  return level;
}

export function rampHold(
  model: LoadModel,
  level: number,
  rampS: number,
  holdS: number,
  options: Omit<LoadProfile, "model" | "schedule"> = {},
): LoadProfile {
  const stages: Stage[] = [];
  if (rampS > 0) stages.push({ over_s: rampS, to_level: level, kind: "ramp" });
  stages.push({ over_s: holdS, to_level: level, kind: "hold" });
  return { model, schedule: { start_level: 0, stages }, ...options };
}

export function loadLabel(load: LoadProfile): string {
  return `${load.model}/${peakLevel(load.schedule)}${load.model === "open" ? "rps" : "c"}`;
}

export function resourceLabel(resource: import("./model").ResourceProfile): string {
  const parts: string[] = [];
  if (resource.workers !== undefined) parts.push(`w${resource.workers}`);
  if (resource.memory) parts.push(resource.memory);
  if (resource.cpu) parts.push(`cpu${resource.cpu}`);
  return parts.join("/") || "default";
}

export function pacingWait(pacing: Pacing | undefined, fireS: number): number {
  if (!pacing || pacing.kind === "none") return 0;
  if (pacing.kind === "constant") return pacing.secs;
  if (pacing.kind === "constant_pacing") return Math.max(0, pacing.secs - fireS);
  return pacing.secs + Math.random() * Math.max(0, pacing.max_secs - pacing.secs);
}
