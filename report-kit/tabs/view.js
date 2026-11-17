document.querySelectorAll('.report-tabs').forEach(list => {
  const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
  function activate(tab) {
    tabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !selected;
    });
    // Charts initialized in a hidden panel need their visible dimensions on activation.
    document.querySelectorAll('[role="tabpanel"]:not([hidden]) .chart').forEach(el => {
      globalThis.echarts?.getInstanceByDom(el)?.resize();
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      tabs[next].focus();
      activate(tabs[next]);
    });
  });
});
