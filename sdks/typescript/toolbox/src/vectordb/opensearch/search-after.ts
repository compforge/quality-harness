import type { SearchEngine, SearchQuery } from "./types";

export interface SearchAfterOptions {
  query: SearchQuery;
  /** The caller must include a stable tie breaker in this sort. */
  sort: readonly Record<string, unknown>[];
  pageSize: number;
  signal?: AbortSignal;
}

/** Iterate raw hits with the last hit's sort tuple; caller owns the stable sort and any PIT.
 * Without PIT, this is a live view and concurrent indexing can change later pages.
 */
export async function* searchAfterPages(
  search: SearchEngine,
  index: string,
  options: SearchAfterOptions,
): AsyncGenerator<Record<string, unknown>[]> {
  if (!Number.isSafeInteger(options.pageSize) || options.pageSize <= 0 || !options.sort.length) {
    throw new Error("search_after requires a positive pageSize and a non-empty sort");
  }
  let cursor: unknown[] | undefined;
  for (;;) {
    options.signal?.throwIfAborted();
    const body: SearchQuery = {
      query: options.query,
      sort: options.sort,
      size: options.pageSize,
      track_total_hits: false,
      ...(cursor ? { search_after: cursor } : {}),
    };
    const result = await search.search(index, body);
    if (result.timed_out === true || Number((result._shards as Record<string, unknown> | undefined)?.failed ?? 0) > 0) {
      throw new Error(`OpenSearch search_after page failed for index '${index}'`);
    }
    const hits = (result.hits as Record<string, unknown> | undefined)?.hits;
    if (!Array.isArray(hits)) throw new Error(`OpenSearch search_after response has no hits for index '${index}'`);
    if (!hits.length) return;
    const page = hits as Record<string, unknown>[];
    const next = page.at(-1)?.sort;
    if (!Array.isArray(next) || !next.length || (cursor && JSON.stringify(next) === JSON.stringify(cursor))) {
      throw new Error(`OpenSearch search_after cursor did not advance for index '${index}'`);
    }
    yield page;
    cursor = next;
    if (page.length < options.pageSize) return;
  }
}
