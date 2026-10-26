/* Native forms and section anchors work without JavaScript. */
(() => {
  'use strict';
  const toggle = document.querySelector('[data-nav-toggle]');
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.querySelector('[data-close-nav]');
  if (!toggle || !sidebar || !backdrop) return;
  const media = window.matchMedia('(max-width: 760px)');
  function setOpen(open) {
    document.body.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
    backdrop.hidden = !open;
    if (media.matches) sidebar.inert = !open;
    if (open) sidebar.querySelector('a')?.focus();
  }
  function syncSize() {
    setOpen(false);
    sidebar.inert = media.matches;
  }
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  backdrop.addEventListener('click', () => { setOpen(false); toggle.focus(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('nav-open')) {
      setOpen(false); toggle.focus();
    }
    if (event.key === 'Tab' && document.body.classList.contains('nav-open')) {
      const focusable = [...sidebar.querySelectorAll('a, button')];
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  sidebar.addEventListener('click', event => {
    if (media.matches && event.target.closest('a')) setOpen(false);
  });
  media.addEventListener('change', syncSize);
  document.documentElement.classList.add('js-nav');
  syncSize();
})();

(() => {
  'use strict';
  const root = document.querySelector('[data-project-tabs]');
  if (!root) return;
  const tablist = root.querySelector('[role="tablist"]');
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
  if (panels.some(panel => !panel || !root.contains(panel))) return;

  function requestedId() {
    const hash = window.location.hash.slice(1);
    return tabs.some(tab => tab.getAttribute('aria-controls') === hash) ? hash : 'overview';
  }

  function activate(id, { focusTab = false, focusPanel = false } = {}) {
    tabs.forEach((tab, index) => {
      const selected = tab.getAttribute('aria-controls') === id;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[index].hidden = !selected;
      if (selected) {
        tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        if (focusTab) tab.focus();
        if (focusPanel) panels[index].querySelector('[data-panel-title]')?.focus({ preventScroll: true });
      }
    });
  }

  function navigate(id, options = {}) {
    const { replace = false, ...focus } = options;
    const hash = `#${id}`;
    if (window.location.hash !== hash || replace) {
      window.history[replace ? 'replaceState' : 'pushState'](null, '', hash);
    }
    activate(id, focus);
  }

  function syncFromLocation() {
    const id = requestedId();
    if (window.location.hash !== `#${id}`) window.history.replaceState(null, '', `#${id}`);
    activate(id, { focusPanel: true });
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', event => {
      event.preventDefault();
      navigate(tab.getAttribute('aria-controls'), { focusPanel: true });
    });
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else if (event.key === ' ') next = index;
      else return;
      event.preventDefault();
      navigate(tabs[next].getAttribute('aria-controls'), { focusTab: true });
    });
  });

  window.addEventListener('hashchange', syncFromLocation);
  window.addEventListener('popstate', syncFromLocation);
  root.classList.add('project-tabs-enhanced');
  const initial = requestedId();
  if (window.location.hash !== `#${initial}`) window.history.replaceState(null, '', `#${initial}`);
  activate(initial);
})();
