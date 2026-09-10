import { createReadStream } from "node:fs";
import { readFile } from "node:fs/promises";
import { createInterface } from "node:readline";
import { join } from "node:path";
import { EvidenceStore } from "./loading/store";

/** Fixed trace membership; observations survive runs, computed facts do not. */
export class Dataset {
  constructor(readonly id: string, readonly source: string, readonly path: string, readonly count: number) {}
  async *members(): AsyncIterable<string> {
    const stream = createReadStream(join(this.path, "members.jsonl"), { encoding: "utf8" });
    const lines = createInterface({ input: stream, crlfDelay: Infinity });
    try { for await (const line of lines) if (line) yield JSON.parse(line) as string; }
    finally { lines.close(); stream.destroy(); }
  }
  async contains(traceId: string): Promise<boolean> {
    return await new EvidenceStore(join(this.path, "members")).get<string>(traceId) === traceId;
  }
  static async load(path: string): Promise<Dataset> {
    const manifest = JSON.parse(await readFile(join(path, "manifest.json"), "utf8"));
    return new Dataset(manifest.id, manifest.source, path, manifest.count);
  }
}
