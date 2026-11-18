(() => {
  const layout = document.currentScript.previousElementSibling;
  const menu = layout.querySelector('nav');
  const sections = Array.from(layout.querySelector('.perf-content').children)
    .filter(element => element.tagName === 'DETAILS');
  const links = sections.map((section, index) => {
    const summary = section.querySelector('summary');
    section.id = (menu.id || 'perf') + '-section-' + index;
    const link = document.createElement('a');
    link.href = '#' + section.id;
    link.textContent = summary.textContent;
    link.addEventListener('click', event => {
      event.preventDefault();
      section.open = true;
      summary.focus({preventScroll:true});
      section.scrollIntoView({block:'start'});
      activate(index);
    });
    menu.append(link);
    return link;
  });
  function activate(index) {
    links.forEach((link, i) => {
      if (i === index) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  activate(0);
  let scheduled = false;
  window.addEventListener('scroll', () => {
    if (scheduled || !layout.offsetWidth) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      const index = sections.findIndex(section => section.getBoundingClientRect().top > 20);
      activate(index < 0 ? links.length - 1 : Math.max(0, index - 1));
    });
  }, {passive:true});
})();
