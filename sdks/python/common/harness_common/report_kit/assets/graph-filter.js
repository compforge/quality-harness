/* Keep only allowed nodes reachable from the original, pinned entries. */
function rkFilterGraph(frame, selectedGroups) {
  const entries = new Set(frame.entry_ids);
  const allowed = new Set(frame.nodes.filter(n => entries.has(n.id) || selectedGroups.has(n.group)).map(n => n.id));
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
