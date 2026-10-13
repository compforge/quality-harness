import type { WindowedCall, TimelineCall } from "../analyze/call-breakdown";

export function measurementTimelineRows(calls: readonly WindowedCall[], collapsed: ReadonlySet<string> = new Set(), filter: string | null = null): TimelineCall[] {
  const byKey = new Map(calls.map(call => [call.key, call]));
  const children = new Map<string | null, WindowedCall[]>();
  for (const call of calls) {
    const parent = call.parent && byKey.has(call.parent) ? call.parent : null;
    const list = children.get(parent) ?? []; list.push(call); children.set(parent, list);
  }
  for (const list of children.values()) list.sort((a,b) => a.start - b.start || b.end - a.end || a.key.localeCompare(b.key));
  const visible = new Set<string>();
  if (filter) for (const call of calls) if (call.group === filter) {
    for (let item: WindowedCall | undefined = call; item && !visible.has(item.key); item = item.parent ? byKey.get(item.parent) : undefined) visible.add(item.key);
  }
  const rows: TimelineCall[] = [];
  const visit = (parent: string | null, depth: number) => {
    for (const call of children.get(parent) ?? []) {
      if (filter && !visible.has(call.key)) continue;
      rows.push({ ...call, depth, hasChildren: (children.get(call.key) ?? []).length > 0 });
      if (filter || !collapsed.has(call.key)) visit(call.key, depth + 1);
    }
  };
  visit(null, 0);
  return rows;
}
