import { formatMs } from "../kinds/base";
import type { View, ViewPage } from "./model";
const quote = (value: unknown) => JSON.stringify(value);
const page = (value: ViewPage<unknown>) => `${value.items.length}/${value.total} rows; offset=${value.offset}; next_offset=${value.next_offset ?? "none"}`;

export function renderText(view: View): string {
  const lines = [`trace=${view.trace_id} ${view.scope} view=${view.view}`];
  if (view.scope === "trace") {
    lines.push(`status=${view.status}; ${page(view.rows)}`);
    for (const row of view.rows.items) lines.push(`${"  ".repeat(row.depth)}${row.kind} ${quote(row.name)} service=${quote(row.service ?? "")} start_ms=${row.start_ms} duration=${row.timing_known === false ? "unknown" : formatMs(row.duration_ms)} id=${quote(row.node_id)} parent=${quote(row.parent_id ?? "")} nodes=${quote(row.source_node_ids)} spans=${quote(row.span_ids)}${Array.isArray(row.findings) && row.findings.length ? ` findings=${quote(row.findings)}` : ""}`);
    lines.push(...view.measurements.map(quote));
  } else {
    const node = view.node;
    lines.push(`node=${quote(node.node_id)} ${quote(node.name)} duration=${formatMs(node.duration_ms)} spans=${quote(node.span_ids)}`);
    if (view.view === "default") {
      lines.push(`facts=${quote(node.facts ?? {})}`, `details=${quote(node.details ?? {})}`, `findings=${quote(node.findings ?? [])}`, `spans=${quote(view.spans)}`);
    } else {
      const timing = view.timing;
      lines.push(`timing=${timing?.status ?? "not_prepared"}${timing?.error ? ` error=${quote(timing.error)}` : ""}`);
      if (timing?.window) lines.push(`Earliest observed trace start → node end: ${formatMs(timing.window.elapsed_ms)} wall-clock (${timing.window.start_ms}..${timing.window.end_ms} ms)`, "Nested/parallel calls and kinds overlap; sums are not causal wall-clock contributions.");
      if (view.view === "measurements") {
        lines.push(...view.measurements.map(quote));
        if (view.groups) {
          lines.push(`Call groups: ${page(view.groups)} (durations in ms)`);
          for (const group of view.groups.items) lines.push(`${group.kind} ${quote(group.service)} ${quote(group.target)} ${quote(group.name)} count=${group.count} sum_ms=${group.total} avg_ms=${group.average} max_ms=${group.max} key=${quote(group.key)}`);
        }
      } else if (view.calls) {
        lines.push(`Timeline: ${page(view.calls)} (offsets relative to window start)`);
        for (const call of view.calls.items) lines.push(`${call.kind} ${quote(call.service)} ${quote(call.name)} +${formatMs(call.start)}..+${formatMs(call.end)} duration=${formatMs(call.duration)} clipped=${call.clipped} key=${quote(call.key)} parent=${quote(call.parent ?? "")} node=${quote(call.node_id)} spans=${quote(call.span_ids)}`);
      }
    }
  }
  return lines.join("\n") + "\n";
}
