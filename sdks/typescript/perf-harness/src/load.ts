export interface Stage {
  duration_s: number;
  request_rate: number;
  max_inflight: number;
  kind: "hold" | "ramp" | "warmup";
  name?: string;
}

export interface Warmup {
  step_s?: number;
}

export interface LoadPlan {
  request_rate: number;
  max_inflight: number;
  hold_s?: number;
  warmup?: Warmup;
  stages?: Stage[];
  arrival?: "constant" | "poisson";
  seed?: number;
  abort_on_error_rate?: number;
  breaker_min_n?: number;
  cooldown_timeout_s?: number;
}

export function stages(load: LoadPlan): Stage[] {
  if (load.stages?.length) return load.stages;
  const result: Stage[] = [];
  const step = load.warmup?.step_s ?? 5;
  if (!saturated(load) && step) {
    for (
      let rate = Math.min(1, load.request_rate);
      rate < load.request_rate;
      rate = Math.min(rate * 2, load.request_rate)
    ) {
      result.push({
        kind: "warmup", duration_s: step,
        request_rate: rate, max_inflight: load.max_inflight,
      });
    }
  }
  result.push({
    kind: "hold", duration_s: load.hold_s ?? 60,
    request_rate: load.request_rate, max_inflight: load.max_inflight,
  });
  return result;
}
export function duration(load: LoadPlan): number {
  return stages(load).reduce((total, stage) => total + stage.duration_s, 0);
}
export function saturated(load: LoadPlan): boolean {
  return load.request_rate === Infinity;
}
export function level(
  stage: Pick<Stage, "request_rate" | "max_inflight">,
): number {
  return stage.request_rate === Infinity
    ? stage.max_inflight
    : stage.request_rate;
}
export function peakLevel(load: LoadPlan): number {
  return Math.max(...stages(load).map(level));
}
export function stageLabel(stage: Stage): string {
  return stage.name ?? `${stage.kind}@${level(stage)}`;
}
export function validateLoadPlan(load: LoadPlan): void {
  if (!load.stages?.length && !(load.request_rate > 0))
    throw new Error("request_rate must be > 0");
  if (!(load.request_rate >= 0)) throw new Error("invalid request_rate");
  if (!Number.isFinite(load.hold_s ?? 60) || (load.hold_s ?? 60) <= 0)
    throw new Error("hold_s must be finite and > 0");
  if (!Number.isFinite(load.warmup?.step_s ?? 5) || (load.warmup?.step_s ?? 5) < 0)
    throw new Error("warmup.step_s must be finite and >= 0");
  for (const stage of [{ ...load, duration_s: load.hold_s ?? 60, kind: "hold" }, ...stages(load)]) {
    if (!Number.isFinite(stage.duration_s) || stage.duration_s <= 0)
      throw new Error("duration_s must be finite and > 0");
    if (!(stage.request_rate >= 0) || stage.request_rate === -Infinity)
      throw new Error("request_rate must be >= 0 or inf");
    if (!Number.isInteger(stage.max_inflight) || stage.max_inflight < 1)
      throw new Error("max_inflight must be an integer >= 1");
    if (stage.kind !== "hold" && stage.kind !== "ramp" && stage.kind !== "warmup")
      throw new Error("stage.kind must be hold, ramp or warmup");
    if ((stage.request_rate === Infinity) !== saturated(load))
      throw new Error("stages cannot switch between finite rate and inf");
  }
  if (
    !Number.isFinite(load.cooldown_timeout_s ?? 180) ||
    (load.cooldown_timeout_s ?? 180) < 0
  )
    throw new Error("invalid cooldown_timeout_s");
  if (load.arrival && !["constant", "poisson"].includes(load.arrival))
    throw new Error("invalid arrival distribution");
  if (
    !Number.isInteger(load.breaker_min_n ?? 20) ||
    (load.breaker_min_n ?? 20) < 1
  )
    throw new Error("invalid breaker_min_n");
  if (
    load.abort_on_error_rate !== undefined &&
    !(load.abort_on_error_rate > 0 && load.abort_on_error_rate <= 1)
  )
    throw new Error("invalid abort_on_error_rate");
}
export function target(load: LoadPlan, t: number): [number, number] {
  let clock = 0,
    rate = load.request_rate,
    concurrency = load.max_inflight;
  for (const s of stages(load)) {
    if (t < clock + s.duration_s) {
      if (s.kind !== "ramp") return [s.request_rate, s.max_inflight];
      const f = Math.max(0, t - clock) / s.duration_s;
      return [
        saturated(load) ? Infinity : rate + (s.request_rate - rate) * f,
        Math.max(
          1,
          Math.floor(concurrency + (s.max_inflight - concurrency) * f),
        ),
      ];
    }
    clock += s.duration_s;
    rate = s.request_rate;
    concurrency = s.max_inflight;
  }
  return [rate, concurrency];
}
export function arrivalTime(load: LoadPlan, volume: number): number {
  let clock = 0,
    previous = load.request_rate;
  for (const s of stages(load)) {
    const start = s.kind === "ramp" ? previous : s.request_rate;
    const slope = (s.request_rate - start) / s.duration_s;
    const mass = ((start + s.request_rate) * s.duration_s) / 2;
    if (volume < mass) {
      if (Math.abs(slope) < 1e-12) return clock + volume / start;
      return (
        clock +
        (volume
          ? (2 * volume) /
            (start + Math.sqrt(Math.max(0, start * start + 2 * slope * volume)))
          : 0)
      );
    }
    volume -= mass;
    clock += s.duration_s;
    previous = s.request_rate;
  }
  return Infinity;
}
export function loadLabel(load: LoadPlan): string {
  return `${saturated(load) ? "concurrency" : "rate"}/${peakLevel(load)}/c${Math.max(load.max_inflight, ...stages(load).map((s) => s.max_inflight))}`;
}
export function resourceLabel(
  resource: import("./model").ResourceProfile,
): string {
  return (
    [
      resource.workers === undefined ? null : `w${resource.workers}`,
      resource.memory,
      resource.cpu ? `cpu${resource.cpu}` : null,
    ]
      .filter(Boolean)
      .join("/") || "default"
  );
}

// Normalized configuration shared by identity hashing and persisted Arm facts.
export function serializeLoadPlan(load: LoadPlan) {
  return {
    ...load,
    request_rate: load.request_rate === Infinity ? "inf" : load.request_rate,
    stages: (load.stages ?? []).map((s) => ({
      ...s,
      request_rate: s.request_rate === Infinity ? "inf" : s.request_rate,
    })),
    arrival: load.arrival ?? "constant",
    seed: load.seed ?? 0,
    hold_s: load.hold_s ?? 60,
    warmup: { step_s: load.warmup?.step_s ?? 5 },
    abort_on_error_rate: load.abort_on_error_rate ?? null,
    breaker_min_n: load.breaker_min_n ?? 20,
    cooldown_timeout_s: load.cooldown_timeout_s ?? 180,
  };
}
