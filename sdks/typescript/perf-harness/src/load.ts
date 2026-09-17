export interface Stage {
  duration_s: number;
  request_rate: number;
  max_concurrency: number;
  kind: "hold" | "ramp";
  name?: string;
}

export interface LoadPlan {
  request_rate: number;
  max_concurrency: number;
  duration_s: number;
  stages?: Stage[];
  arrival?: "constant" | "poisson";
  seed?: number;
  warmup_s?: number;
  abort_on_error_rate?: number;
  breaker_min_n?: number;
  drain_timeout_s?: number;
}

export function stages(load: LoadPlan): Stage[] {
  return load.stages?.length
    ? load.stages
    : [
        {
          duration_s: load.duration_s,
          request_rate: load.request_rate,
          max_concurrency: load.max_concurrency,
          kind: "hold",
        },
      ];
}
export function saturated(load: LoadPlan): boolean {
  return load.request_rate === Infinity;
}
export function level(
  stage: Pick<Stage, "request_rate" | "max_concurrency">,
): number {
  return stage.request_rate === Infinity
    ? stage.max_concurrency
    : stage.request_rate;
}
export function peakLevel(load: LoadPlan): number {
  return Math.max(...stages(load).map(level));
}
export function stageLabel(stage: Stage): string {
  return stage.name ?? `${stage.kind}@${level(stage)}`;
}
export function validateLoadPlan(load: LoadPlan): void {
  for (const stage of [{ ...load, kind: "hold" }, ...stages(load)]) {
    if (!Number.isFinite(stage.duration_s) || stage.duration_s <= 0)
      throw new Error("duration_s must be finite and > 0");
    if (!(stage.request_rate >= 0) || stage.request_rate === -Infinity)
      throw new Error("request_rate must be >= 0 or inf");
    if (!Number.isInteger(stage.max_concurrency) || stage.max_concurrency < 1)
      throw new Error("max_concurrency must be an integer >= 1");
    if (stage.kind !== "hold" && stage.kind !== "ramp")
      throw new Error("stage.kind must be hold or ramp");
    if ((stage.request_rate === Infinity) !== saturated(load))
      throw new Error("stages cannot switch between finite rate and inf");
  }
  if (
    Math.abs(
      stages(load).reduce((a, s) => a + s.duration_s, 0) - load.duration_s,
    ) > 1e-9
  )
    throw new Error("stage durations must sum to duration_s");
  if (
    !Number.isFinite(load.warmup_s ?? 0) ||
    (load.warmup_s ?? 0) < 0 ||
    (load.warmup_s ?? 0) >= load.duration_s
  )
    throw new Error("invalid warmup_s");
  if (
    !Number.isFinite(load.drain_timeout_s ?? 30) ||
    (load.drain_timeout_s ?? 30) < 0
  )
    throw new Error("invalid drain_timeout_s");
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
    concurrency = load.max_concurrency;
  for (const s of stages(load)) {
    if (t < clock + s.duration_s) {
      if (s.kind === "hold") return [s.request_rate, s.max_concurrency];
      const f = Math.max(0, t - clock) / s.duration_s;
      return [
        saturated(load) ? Infinity : rate + (s.request_rate - rate) * f,
        Math.max(
          1,
          Math.floor(concurrency + (s.max_concurrency - concurrency) * f),
        ),
      ];
    }
    clock += s.duration_s;
    rate = s.request_rate;
    concurrency = s.max_concurrency;
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
  return `${saturated(load) ? "concurrency" : "rate"}/${peakLevel(load)}/c${Math.max(load.max_concurrency, ...stages(load).map((s) => s.max_concurrency))}`;
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
    warmup_s: load.warmup_s ?? 0,
    abort_on_error_rate: load.abort_on_error_rate ?? null,
    breaker_min_n: load.breaker_min_n ?? 20,
    drain_timeout_s: load.drain_timeout_s ?? 30,
  };
}
