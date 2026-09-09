import type { TraceContext } from "../model/context";
import type { Finding, Node } from "../model/node";
import type { Findings } from "./diagnose";

export type Detector = (node: Node, context: TraceContext, found: Findings) => Finding[];

export class DetectorRegistry {
  readonly #detectors: Detector[];

  constructor(detectors: Iterable<Detector> = []) {
    this.#detectors = [...detectors];
  }

  register(detector: Detector): Detector {
    this.#detectors.push(detector);
    return detector;
  }

  registered(): Detector[] {
    return [...this.#detectors];
  }
}
