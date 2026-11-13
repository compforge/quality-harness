/* Neutral report graph renderer. No remote data access or domain calculations. */
function rkGraph(root, view) {
  const select = root.querySelector('select');
  const details = root.querySelector('.graph-details');
  const canvas = root.querySelector('.graph-canvas');
  const zoom = root.querySelector('.graph-zoom input');
  const zoomValue = root.querySelector('.graph-zoom output');
  let needsLayout = true;
  view.frames.forEach((frame, index) => {
    const option = document.createElement('option');
    option.value = String(index); option.textContent = frame.label;
    select.appendChild(option);
  });
  const cy = cytoscape({ container: canvas, elements: [], minZoom: 0.001, maxZoom: 1,
    style: [
      { selector: 'node', style: { 'label': 'data(label)', 'shape': 'round-rectangle',
        'width': 310, 'height': 110, 'text-wrap': 'wrap', 'text-max-width': 290,
        'text-valign': 'center', 'font-size': 13, 'background-color': 'data(color)',
        'border-width': 1, 'border-color': '#94a3b8' } },
      { selector: 'node:parent', style: { 'text-valign': 'top', 'text-halign': 'center',
        'padding': 25, 'background-opacity': 0.18, 'font-weight': 'bold' } },
      { selector: 'edge', style: { 'label': 'data(label)', 'font-size': 11,
        'curve-style': 'bezier', 'target-arrow-shape': 'triangle',
        'line-color': '#64748b', 'target-arrow-color': '#64748b', 'width': 1.5 } },
      { selector: 'edge.conditional', style: { 'line-style': 'dashed' } },
      { selector: 'node:selected', style: { 'border-color': '#2563eb', 'border-width': 3 } }
    ]
  });
  function syncZoom() {
    zoom.value = String(Math.round(cy.zoom() * 100));
    zoomValue.textContent = `${Number((cy.zoom() * 100).toFixed(1))}%`;
  }
  /**
   * @why Hidden panels have no usable viewport; defer layout until visible so
   * opening a tab or a collapsed section never fits the graph into zero dimensions.
   */
  function resize() {
    if (!canvas.clientWidth || !canvas.clientHeight) return;
    cy.resize();
    if (needsLayout) {
      cy.layout({name: 'dagre', rankDir: 'LR', nodeSep: 35, rankSep: 100, padding: 30}).run();
      needsLayout = false;
    }
    syncZoom();
  }
  function showFrame() {
    const frame = view.frames[Number(select.value)];
    if (!frame) return;
    root.querySelectorAll('[data-graph-frame]').forEach(panel => {
      panel.hidden = panel.dataset.graphFrame !== select.value;
    });
    cy.elements().remove();
    const ids = new Set([...frame.nodes, ...frame.edges].map(item => item.id));
    const groups = new Map();
    for (const node of frame.nodes) {
      if (!node.group || groups.has(node.group)) continue;
      let id = 'report-group:' + node.group;
      while (ids.has(id)) id = ':' + id;
      ids.add(id);
      groups.set(node.group, id);
      cy.add({data: {id, label: node.group, color: node.color, details: [['Group', node.group]]}});
    }
    cy.add(frame.nodes.map(node => ({data: {...node, parent: groups.get(node.group)}})));
    cy.add(frame.edges.map(edge => ({data: edge, classes: edge.dashed ? 'conditional' : ''})));
    needsLayout = true;
    resize();
    details.textContent = 'Select a node for details.';
  }
  cy.on('tap', 'node', event => {
    const node = event.target.data();
    details.textContent = node.details.map(item => item[0] + ': ' + item[1]).join('\n');
  });
  select.addEventListener('change', showFrame);
  root.querySelector('.graph-fit').addEventListener('click', () => cy.fit(undefined, 30));
  cy.on('zoom', syncZoom);
  zoom.addEventListener('input', () => {
    cy.zoom({level: Math.max(cy.minZoom(), Number(zoom.value) / 100),
      renderedPosition: {x: canvas.clientWidth / 2, y: canvas.clientHeight / 2}});
  });
  new ResizeObserver(resize).observe(canvas);
  showFrame();
}
