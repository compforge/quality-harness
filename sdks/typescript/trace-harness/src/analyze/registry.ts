import type { AnalysisContext } from "./context";
import type { Finding, Node } from "../model/node";

export type Detector = (node: Node, context: AnalysisContext) => Finding[];

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
