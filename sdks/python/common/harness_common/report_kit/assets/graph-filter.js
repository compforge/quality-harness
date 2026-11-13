/* Compare prepared numeric values; never parse formatted node labels. */
function rkEntryValue(frame) {
  if (frame.entry_ids.length !== 1) return null;
  const value = frame.nodes.find(n => n.id === frame.entry_ids[0])?.comparison_value;
  return Number.isFinite(value) && value >= 0 ? value : null;
}
function rkValueMatches(value, reference, selected) {
  if (!Number.isFinite(value) || value < 0) return selected.has('N/A');
  if (value === 0) return selected.has('= 0');
  // @spec Missing/ambiguous entry values disable relative filtering, retaining positive nodes.
  if (reference === null) return true;
  return selected.has(value < reference ? '< 入口' : '≥ 入口');
}

/* Keep only allowed nodes reachable from the original, pinned entries. */
function rkFilterGraph(frame, selectedGroups, selectedImportance = new Set(['high', 'medium', 'low']),
    selectedValues = new Set(['= 0', 'N/A', '< 入口', '≥ 入口'])) {
  const entries = new Set(frame.entry_ids);
  const reference = rkEntryValue(frame);
  const allowed = new Set(frame.nodes.filter(n => entries.has(n.id) || (selectedGroups.has(n.group) && selectedImportance.has(n.importance || 'medium') &&
    rkValueMatches(n.comparison_value, reference, selectedValues))).map(n => n.id));
  const outgoing = new Map();
  for (const edge of frame.edges) {
    if (!allowed.has(edge.source) || !allowed.has(edge.target)) continue;
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    outgoing.get(edge.source).push(edge.target);
  }
  const reached = new Set([...entries].filter(id => allowed.has(id)));
  const queue = [...reached];
  for (let index = 0; index < queue.length; index++) {
    for (const target of outgoing.get(queue[index]) || []) {
      if (!reached.has(target)) { reached.add(target); queue.push(target); }
    }
  }
  // @spec No shortcut edges: alternate retained paths keep shared descendants visible.
  return {...frame, nodes: frame.nodes.filter(n => reached.has(n.id)),
    edges: frame.edges.filter(e => reached.has(e.source) && reached.has(e.target))};
}
