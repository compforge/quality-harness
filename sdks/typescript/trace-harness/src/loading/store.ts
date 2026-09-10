import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { NormSpan } from "../model/span";

export const keyHash = (key: string): string => createHash("sha256").update(key).digest("hex");

/** Atomic objects on disk; no dataset-sized in-memory cache. */
export class EvidenceStore {
  constructor(readonly path: string) {}
  async get<T>(key: string): Promise<T | undefined> {
    try { return JSON.parse(await readFile(join(this.path, keyHash(key)), "utf8")) as T; }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; }
  }
  async put(key: string, value: unknown): Promise<void> {
    await mkdir(this.path, { recursive: true });
    const target = join(this.path, keyHash(key));
    const temp = `${target}.${randomUUID()}.tmp`;
    try { await writeFile(temp, JSON.stringify(value)); await rename(temp, target); }
    finally { await rm(temp, { force: true }); }
  }
}

export function cloneSpan(value: NormSpan): NormSpan {
  const copy = structuredClone(value);
  return Object.assign(new NormSpan(copy.span_id, copy.parent_span_id, copy.name, copy.start_ms,
    copy.dur_ms, copy.service, copy.has_error, copy.attrs, copy.raw, copy.error_events, copy.events), copy);
}
export const cloneSpans = (spans: Map<string, NormSpan>): Map<string, NormSpan> =>
  new Map([...spans].map(([id, span]) => [id, cloneSpan(span)]));
