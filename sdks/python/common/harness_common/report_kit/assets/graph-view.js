/* Neutral report graph renderer. No remote data access or domain calculations. */
function rkGraph(root, view) {
  const select = root.querySelector('select');
  const details = root.querySelector('.graph-details');
  const canvas = root.querySelector('.graph-canvas');
  view.frames.forEach((frame, index) => {
    const option = document.createElement('option');
    option.value = String(index); option.textContent = frame.label;
    select.appendChild(option);
  });
  const cy = cytoscape({ container: canvas, elements: [],
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
  function showFrame() {
    const frame = view.frames[Number(select.value)];
    if (!frame) return;
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
    cy.layout({name: 'dagre', rankDir: 'LR', nodeSep: 35, rankSep: 100, padding: 30}).run();
    details.textContent = 'Select a node for details.';
  }
  cy.on('tap', 'node', event => {
    const node = event.target.data();
    details.textContent = node.details.map(item => item[0] + ': ' + item[1]).join('\n');
  });
  select.addEventListener('change', showFrame);
  root.querySelector('button').addEventListener('click', () => cy.fit(undefined, 30));
  new ResizeObserver(() => cy.resize()).observe(canvas);
  showFrame();
}
