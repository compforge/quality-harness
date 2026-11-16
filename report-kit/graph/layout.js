/* Domain-neutral, offline two-level layout. ELK computes positions, never graph facts. */
async function rkLayout(nodes, edges, viewport, elk) {
  if (!nodes.length) return {positions: new Map(), width: 0, height: 0};
  const aspect = viewport.width / viewport.height;
  const groups = new Map();
  const owners = new Map();
  for (const node of nodes) {
    const key = node.group ? `group:${node.group}` : `node:${node.id}`;
    if (!groups.has(key)) groups.set(key, {id: `layout:${groups.size}`, nodes: []});
    groups.get(key).nodes.push(node);
    owners.set(node.id, groups.get(key).id);
  }
  const candidates = [];
  // @why Packing parallel operations separately avoids a service's fan-out becoming
  // one unbounded rank. Connected operations still receive directed layered layout.
  // @link component://docs/report-graph.md
  for (const ratio of [Math.max(0.5, aspect / 2), Math.max(1, aspect)]) {
    const boxes = [];
    for (const group of groups.values()) {
      const ids = new Set(group.nodes.map(n => n.id));
      const internal = edges.filter(e => ids.has(e.source) && ids.has(e.target));
      const packed = await elk.layout({id: group.id, layoutOptions: {
        'elk.algorithm': internal.length ? 'layered' : 'rectpacking',
        'elk.direction': 'RIGHT', 'elk.aspectRatio': String(ratio),
        'elk.spacing.nodeNode': '24',
        'elk.padding': '[top=44,left=28,bottom=28,right=28]',
        'elk.rectpacking.widthApproximation.targetWidth': '-1'
      }, children: group.nodes.map(n => ({id: n.id, width: n.width, height: n.height})),
      edges: internal.map((e, i) => ({id: `internal:${i}`, sources: [e.source], targets: [e.target]}))});
      boxes.push(packed);
    }
    const links = new Map();
    for (const edge of edges) {
      const source = owners.get(edge.source), target = owners.get(edge.target);
      if (source === target) continue;
      const key = JSON.stringify([source, target]);
      if (!links.has(key)) links.set(key, {id: `link:${links.size}`, sources: [source], targets: [target]});
    }
    for (const direction of ['RIGHT', 'DOWN']) {
      const outer = await elk.layout({id: 'root', layoutOptions: {
        'elk.algorithm': 'layered', 'elk.direction': direction,
        'elk.aspectRatio': String(aspect), 'elk.spacing.nodeNode': '48',
        'elk.layered.spacing.nodeNodeBetweenLayers': '90',
        'elk.layered.highDegreeNodes.treatment': 'true',
        'elk.padding': '[top=30,left=30,bottom=30,right=30]'
      }, children: boxes.map(b => ({id: b.id, width: b.width, height: b.height})),
      edges: [...links.values()]});
      const positions = new Map();
      const placed = new Map(outer.children.map(b => [b.id, b]));
      for (const box of boxes) {
        const parent = placed.get(box.id);
        for (const node of box.children) positions.set(node.id, {
          x: parent.x + node.x + node.width / 2, y: parent.y + node.y + node.height / 2
        });
      }
      // Maximize readable fit scale for the actual viewport, with deterministic ties.
      candidates.push({positions, width: outer.width, height: outer.height,
        score: Math.min(viewport.width / outer.width, viewport.height / outer.height)});
    }
  }
  return candidates.reduce((best, candidate) => candidate.score > best.score ? candidate : best);
}
