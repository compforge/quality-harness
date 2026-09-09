/** Protocol facts for ordinary HTTP requests, independent of business contributions. */
import type { TraceContext } from "../model/context";
import type { NormSpan } from "../model/span";

const MODEL_URL_MARKS = ["/chat/completions", "/embeddings", "/rerank"];
const HTTP_NAME = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)(?:\s+(\S+))?$/;
type Endpoint = [method: string, target: string, route: string];

function object(value: unknown): Record<string, unknown> {
  for (let i = 0; i < 3 && typeof value === "string"; i++) {
    try { value = JSON.parse(value); } catch { return {}; }
  }
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function endpoint(span: NormSpan): Endpoint | undefined {
  if (span.attr("asgi.event.type")) return undefined;
  const match = HTTP_NAME.exec(span.name);
  const method = span.attr("http.request.method", "http.method") ?? match?.[1];
  if (method === undefined) return undefined;
  const rawUrl = String(span.attr("url.full", "http.url", "http.target", "url.path") ?? match?.[2] ?? "");
  let path = "", query = "", authority = "";
  try {
    if (/^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?\/\//.test(rawUrl)) {
      const parsed = new URL(rawUrl.startsWith("//") ? `http:${rawUrl}` : rawUrl);
      // Preserve explicit ports, as the Python urlsplit implementation does.
      authority = rawUrl.replace(/^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)?\/\//, "").split(/[/?#]/, 1)[0]!.split("@").at(-1)!;
      path = parsed.pathname;
      query = parsed.search;
    } else {
      const withoutFragment = rawUrl.split("#", 1)[0]!;
      const question = withoutFragment.indexOf("?");
      path = question < 0 ? withoutFragment : withoutFragment.slice(0, question);
      query = question < 0 ? "" : withoutFragment.slice(question);
    }
  } catch { return undefined; }
  let route = String(span.attr("http.route") ?? path);
  if (!route) return undefined;
  const action = new URLSearchParams(query).get("Action");
  if (action) route += `?Action=${action}`;
  let target = authority || String(span.attr("server.address", "net.peer.name") ?? span.service ?? "?");
  const port = span.attr("server.port", "net.peer.port");
  if (!authority && port !== undefined) target += `:${port}`;
  return [String(method).toUpperCase(), target, route];
}

function isStream(span: NormSpan): boolean {
  for (const key of ["http.request.header.accept", "http.response.header.content-type", "http.response.header.content_type"]) {
    if (String(span.attr(key) ?? "").toLowerCase().includes("text/event-stream")) return true;
  }
  for (const key of ["http.request.headers", "http.response.headers"]) {
    if (Object.entries(object(span.attr(key))).some(([name, value]) =>
      ["accept", "content-type"].includes(name.toLowerCase())
      && String(value).toLowerCase().includes("text/event-stream")
    )) return true;
  }
  return ["http.request.body", "http.request.body.json"].some((key) => object(span.attr(key)).stream === true);
}

export interface HttpRequest {
  span: NormSpan;
  spans: NormSpan[];
  api: Endpoint;
  ordinary: boolean;
}

export function httpRequests(context: TraceContext): HttpRequest[] {
  const endpoints = new Map<string, Endpoint>();
  for (const span of context.spans.values()) {
    const api = endpoint(span);
    if (api) endpoints.set(span.span_id, api);
  }
  const peers = new Map<string, NormSpan[]>();
  const paired = new Set<string>();
  const children = new Map<string | undefined, NormSpan[]>();
  for (const span of context.spans.values()) {
    const siblings = children.get(span.parent_span_id) ?? [];
    siblings.push(span);
    children.set(span.parent_span_id, siblings);
    const parent = span.parent_span_id ? context.spans.get(span.parent_span_id) : undefined;
    if (endpoints.has(span.span_id) && parent && endpoints.has(parent.span_id)
      && span.attr("span.kind") === "server" && parent.attr("span.kind") === "client") {
      const group = peers.get(parent.span_id) ?? [];
      group.push(span);
      peers.set(parent.span_id, group);
      paired.add(span.span_id);
    }
  }
  const requests: HttpRequest[] = [];
  const view = context.view();
  for (const [id, [method, target, initialRoute]] of endpoints) {
    if (paired.has(id)) continue;
    const span = context.spans.get(id)!;
    const servers = peers.get(id) ?? [];
    const spans = [span, ...servers];
    const route = servers.length ? endpoints.get(servers[0]!.span_id)![2] : initialRoute;
    const evidence = [...spans, ...spans.flatMap((member) => (children.get(member.span_id) ?? []).filter((child) => ["request-body", "response-body"].includes(child.name)))];
    let ordinary = !evidence.some(isStream) && !MODEL_URL_MARKS.some((mark) => route.includes(mark));
    let ancestor: NormSpan | undefined = span;
    const seen = new Set<string>();
    while (ordinary && ancestor && !seen.has(ancestor.span_id)) {
      seen.add(ancestor.span_id);
      if (view.by_span.get(ancestor.span_id)?.kind === "model-call") { ordinary = false; break; }
      ancestor = ancestor.parent_span_id ? context.spans.get(ancestor.parent_span_id) : undefined;
    }
    requests.push({ span, spans, api: [method, target, route], ordinary });
  }
  return requests;
}
