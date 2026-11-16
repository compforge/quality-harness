import type { SearchEngine, SearchQuery } from "./types";
/** OpenSearch-specific read APIs stay outside the engine-neutral SearchEngine contract. */
export interface OpenSearchReadApi extends SearchEngine {
  request(path: string, query?: SearchQuery): Promise<unknown>;
}

export function isOpenSearchReadApi(search: SearchEngine): search is OpenSearchReadApi {
  return typeof (search as Partial<OpenSearchReadApi>).request === "function";
}

/** Safe error-chain facts for logs; driver messages/meta can contain credentials and response bodies. */
export function describeOpenSearchError(error: unknown): string {
  const parts: string[] = [];
  const seen = new Set<unknown>();
  while (error && typeof error === "object" && !seen.has(error) && parts.length < 4) {
    seen.add(error);
    const value = error as { name?: unknown; code?: unknown; kind?: unknown; statusCode?: unknown; meta?: { statusCode?: unknown }; cause?: unknown; message?: unknown };
    const safe = (value: unknown) => typeof value === "number" ? String(value)
      : typeof value === "string" && /^[a-zA-Z0-9_.-]{1,80}$/.test(value) ? value : undefined;
    const argument = (value.code === "ERR_INVALID_ARG_VALUE" || value.code === "ERR_INVALID_ARG_TYPE")
      && typeof value.message === "string"
      ? value.message.match(/^The ["']([a-zA-Z][a-zA-Z0-9_.]{0,79})["'] (?:argument|option|property)/)?.[1]
        ?? value.message.match(/^The (?:argument|option|property) ["']([a-zA-Z][a-zA-Z0-9_.]{0,79})["']/)?.[1]
      : undefined;
    parts.push([`error=${safe(value.name) ?? "Error"}`, argument && `argument=${argument}`, safe(value.code) && `code=${safe(value.code)}`,
      safe(value.kind) && `kind=${safe(value.kind)}`,
      safe(value.statusCode ?? value.meta?.statusCode) && `status=${safe(value.statusCode ?? value.meta?.statusCode)}`].filter(Boolean).join(" "));
    error = value.cause;
  }
  return parts.join(" cause=") || "error=UnknownError";
}

