import { stages, saturated, stageLabel, level, type LoadPlan, type Stage } from "./load";
import type { Window, Phase } from "./model";

/** Owns actual stage boundaries independently of the async scheduler. */
export class LoadController {
  readonly stages: Stage[];
  readonly windows: Window[] = [];
  index = 0;
  start_s = 0;
  hold_start_s?: number;
  end_s?: number;
  private previous: [number, number];
  private last_s = 0;
  private limited = false;

  constructor(readonly load: LoadPlan) {
    this.stages = stages(load);
    this.previous = [load.request_rate, load.max_inflight];
    this.open();
  }

  get done(): boolean {
    return this.end_s !== undefined;
  }

  get phase(): Phase {
    if (this.done) return "cooldown";
    return this.stages[this.index]!.kind === "hold" ? "hold" : "warmup";
  }

  get deadline(): number {
    return this.start_s + this.stages[this.index]!.duration_s;
  }

  private open(): void {
    const stage = this.stages[this.index]!;
    this.windows.push({
      id: `stage-${this.index}`,
      name: stageLabel(stage),
      kind: stage.kind,
      start_s: this.start_s,
      end_s: this.start_s,
      complete: false,
      target_level: level(stage),
      limited_s: 0,
      by_case: {},
      by_facet: {},
      probe_metrics: {},
    });
    if (stage.kind === "hold" && this.hold_start_s === undefined)
      this.hold_start_s = this.start_s;
  }

  target(at: number): [number, number] {
    const stage = this.stages[this.index]!;
    if (stage.kind !== "ramp") return [stage.request_rate, stage.max_inflight];
    const fraction = Math.min(1, Math.max(0, at - this.start_s) / stage.duration_s);
    return [
      saturated(this.load)
        ? Infinity
        : this.previous[0] + (stage.request_rate - this.previous[0]) * fraction,
      Math.max(1, Math.floor(this.previous[1] + (stage.max_inflight - this.previous[1]) * fraction)),
    ];
  }

  advance(at: number, inflight: number, limited = false): void {
    while (!this.done) {
      const stage = this.stages[this.index]!;
      const end = Math.min(at, this.deadline);
      const window = this.windows.at(-1)!;
      if (this.limited)
        window.limited_s = (window.limited_s ?? 0) + Math.max(0, end - this.last_s);
      this.last_s = end;
      window.end_s = end;
      // Cap feedback shortens warmup; hold replenishes at the configured target rate.
      const capped = at < this.deadline && stage.kind === "warmup" && inflight >= stage.max_inflight;
      if (at < this.deadline && !capped) {
        this.limited = limited;
        return;
      }
      let next = this.index + 1;
      if (capped) {
        while (next < this.stages.length && this.stages[next]!.kind !== "hold") next++;
      }
      window.complete = true;
      window.end_reason = capped
        ? "inflight_limit"
        : stage.kind === "warmup" && this.stages[next]?.kind === "hold"
          ? "target_rate"
          : "duration";
      this.previous = [stage.request_rate, stage.max_inflight];
      this.start_s = end;
      if (next === this.stages.length) {
        this.end_s = end;
        return;
      }
      this.index = next;
      this.open();
      this.limited = limited;
    }
  }

  abort(at: number): void {
    if (!this.done) {
      const window = this.windows.at(-1)!;
      window.end_s = Math.min(at, this.deadline);
      window.end_reason = "aborted";
      this.end_s = window.end_s;
    }
  }
}
