let measurementState;
let measurementChart;
function openMeasurements(summary) {
  const node = loadNode(summary);
  const dialog = document.getElementById('measurement-dialog');
  const timing = node.timing;
  const usable = timing?.status === 'measured' && timing.window;
  measurementState = { node, calls: [], groups: [], collapsed: new Set(), filter: null, tab: 'summary', elapsed: 0 };
  document.getElementById('measurement-title').textContent = node.name + ' · 耗时分析';
  document.getElementById('measurement-window').textContent = usable ? '请求开始 → 此节点结束 · ' + fmtMs(timing.window.elapsed_ms) + ' wall-clock' : '此节点的 Measurements';
  document.getElementById('measurement-tabs').hidden = !usable;
  document.getElementById('measurement-call-detail').replaceChildren();
  document.getElementById('measurement-other').innerHTML = measurementBlock({ measurements: (node.measurements || []).filter(row => !usable || row.kind !== 'calls_until_node_end') });
  if (usable) {
    measurementState.calls = timing.calls;
    measurementState.groups = timing.groups;
    measurementState.elapsed = timing.window.elapsed_ms;
    renderMeasurementSummary();
  } else document.getElementById('measurement-summary').replaceChildren();
  dialog.showModal();
  switchMeasurementTab('summary');
}
function renderMeasurementSummary() {
  const groups = measurementState.groups;
  const max = groups[0]?.total || 1;
  document.getElementById('measurement-summary').innerHTML = groups.length ? '<div class="measurement-table-wrap"><table class="measurement-table"><thead><tr><th>服务 / 操作</th><th>Kind</th><th class="numeric">调用次数</th><th class="numeric">累计耗时 ↓</th><th class="numeric">平均耗时</th><th class="numeric">最慢一次</th></tr></thead><tbody>' + groups.map((group, index) => '<tr><th><button class="measurement-operation" data-group="' + index + '"><span>' + esc(group.name) + '</span><small>' + esc((group.service || '未知服务') + (group.target ? ' → ' + group.target : '')) + '</small></button></th><td>' + esc(group.kind) + '</td><td class="numeric">' + group.count + '</td><td class="numeric measurement-total">' + fmtMs(group.total) + '<span class="measurement-bar"><i style="width:' + (group.total / max * 100) + '%"></i></span></td><td class="numeric">' + fmtMs(group.average) + '</td><td class="numeric">' + fmtMs(group.max) + '</td></tr>').join('') + '</tbody></table></div>' : '<p class="empty">此窗口没有调用来源</p>';
  document.querySelectorAll('[data-group]').forEach(button => button.onclick = () => {
    measurementState.filter = groups[Number(button.dataset.group)].key;
    switchMeasurementTab('timeline');
  });
}
function switchMeasurementTab(tab) {
  measurementState.tab = tab;
  document.querySelectorAll('[data-measurement-tab]').forEach(button => {
    const active = button.dataset.measurementTab === tab;
    button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
  });
  document.getElementById('measurement-summary').hidden = tab !== 'summary';
  document.getElementById('measurement-timeline').hidden = tab !== 'timeline';
  if (tab === 'timeline') renderMeasurementTimeline();
}
function renderMeasurementTimeline() {
  if (!globalThis.echarts) {
    const script = document.createElement('script');
    script.textContent = readTraceEntry('measurement-chart.json');
    document.head.appendChild(script); script.remove();
  }
  const state = measurementState;
  const rows = measurementTimelineRows(state.calls, state.collapsed, state.filter);
  const selectedGroup = state.groups.find(group => group.key === state.filter);
  document.getElementById('measurement-filter').textContent = selectedGroup ? selectedGroup.service + ' / ' + selectedGroup.name + ' · ' + selectedGroup.count + ' 次（保留父级上下文）' : '全部调用 · ' + state.calls.length + ' 次 · 点击名称展开/折叠，点击条形查看详情';
  document.getElementById('measurement-clear').hidden = !state.filter;
  const element = document.getElementById('measurement-chart');
  if (!rows.length) {
    measurementChart?.dispose(); measurementChart = undefined;
    element.textContent = '此窗口没有调用来源';
    return;
  }
  element.style.height = Math.max(280, Math.min(window.innerHeight * .5, rows.length * 28 + 95)) + 'px';
  const visibleRows = Math.max(4, Math.floor((element.clientHeight - 95) / 28));
  const byKey = new Map(rows.map(call => [call.key, call]));
  measurementChart ??= echarts.init(element, null, { renderer: 'canvas' });
  measurementChart.resize();
  const left = Math.min(380, Math.max(210, element.clientWidth * .33));
  measurementChart.setOption({
    animation: false,
    grid: { left, right: 55, top: 40, bottom: 55 },
    tooltip: { formatter: params => {
      const call = rows[params.dataIndex];
      return esc(call.service) + ' / ' + esc(call.name) + '<br>' + esc(call.kind) + ' · ' + fmtMs(call.duration) + (call.clipped ? '（截断至节点结束）' : '') + '<br>开始 +' + fmtMs(call.start) + ' → +' + fmtMs(call.end);
    } },
    xAxis: { type: 'value', min: 0, max: Math.max(state.elapsed, .001), position: 'top', axisLabel: { formatter: value => fmtMs(value) }, splitLine: { lineStyle: { color: '#e2e8f0' } } },
    yAxis: { type: 'category', inverse: true, data: rows.map(call => call.key), triggerEvent: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { align: 'left', margin: left - 12, width: left - 25, overflow: 'truncate', fontSize: 11, formatter: value => {
      const call = byKey.get(value);
      return '  '.repeat(call.depth) + (call.hasChildren ? (state.collapsed.has(call.key) && !state.filter ? '▸ ' : '▾ ') : '  ') + call.name;
    } } },
    dataZoom: [{ type: 'slider', xAxisIndex: 0, filterMode: 'none', bottom: 8, height: 20 }, { type: 'inside', xAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false }, { type: 'slider', yAxisIndex: 0, filterMode: 'none', right: 8, width: 16, startValue: 0, endValue: Math.min(rows.length - 1, visibleRows - 1), showDetail: false }, { type: 'inside', yAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: false, moveOnMouseWheel: true, moveOnMouseMove: false }],
    series: [{ type: 'custom', encode: { x: [1, 2], y: 0 }, renderItem: (params, api) => {
      const call = rows[params.dataIndex];
      const start = api.coord([api.value(1), api.value(0)]), end = api.coord([api.value(2), api.value(0)]);
      const rect = echarts.graphic.clipRectByRect({ x: start[0], y: start[1] - 8, width: Math.max(2, end[0] - start[0]), height: 16 }, params.coordSys);
      return rect && { type: 'rect', shape: rect, style: { fill: KCOLOR[call.kind] || '#64748b', opacity: state.filter && call.group !== state.filter ? .22 : 1, stroke: call.clipped ? '#f59e0b' : 'transparent', lineWidth: 2 } };
    }, data: rows.map((call, index) => [index, call.start, call.end]) }],
  }, true);
  measurementChart.off('click');
  measurementChart.on('click', params => {
    if (params.componentType === 'yAxis') {
      const key = params.value;
      state.collapsed.has(key) ? state.collapsed.delete(key) : state.collapsed.add(key);
      renderMeasurementTimeline();
    } else if (params.componentType === 'series') showMeasurementCall(rows[params.dataIndex]);
  });
}
function showMeasurementCall(call) {
  const box = document.getElementById('measurement-call-detail');
  box.innerHTML = '<strong>' + esc(call.name) + '</strong> · ' + esc(call.service) + ' · ' + esc(call.kind) + '<br>开始 +' + fmtMs(call.start) + ' → +' + fmtMs(call.end) + ' · 窗口内耗时 ' + fmtMs(call.duration) + (call.clipped ? '（调用尚未结束，已截断）' : '') + '<br>';
  const button = document.createElement('button'); button.textContent = '定位 Trace 节点';
  button.onclick = () => { document.getElementById('measurement-dialog').close(); showPerspective(TREES.default ? 'default' : perspective); showLayout('tree'); select(call.node_id); };
  box.appendChild(button);
}
function initMeasurementDialog() {
  const dialog = document.getElementById('measurement-dialog');
  document.getElementById('measurement-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { measurementChart?.dispose(); measurementChart = undefined; });
  document.querySelectorAll('[data-measurement-tab]').forEach(button => {
    button.onclick = () => switchMeasurementTab(button.dataset.measurementTab);
    button.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const tab = event.key === 'Home' ? 'summary' : event.key === 'End' ? 'timeline' : measurementState.tab === 'summary' ? 'timeline' : 'summary';
      switchMeasurementTab(tab); document.querySelector('[data-measurement-tab="' + tab + '"]').focus();
    };
  });
  document.getElementById('measurement-clear').onclick = () => { measurementState.filter = null; renderMeasurementTimeline(); };
  document.getElementById('measurement-expand').onclick = () => { measurementState.collapsed.clear(); renderMeasurementTimeline(); };
  document.getElementById('measurement-fold').onclick = () => { measurementState.collapsed = new Set(measurementState.calls.map(call => call.key)); renderMeasurementTimeline(); };
  document.getElementById('measurement-reset').onclick = () => measurementChart?.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: 0, end: 100 });
  window.addEventListener('resize', () => measurementChart?.resize());
}
