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
