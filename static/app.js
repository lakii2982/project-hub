/* Progressive enhancement only: core navigation, tabs and forms work without JavaScript. */
(() => {
  'use strict';
  document.documentElement.classList.add('js-app');
})();

/* PH-026 shell: native <dialog> navigation drawer below 1024px.
   Without JavaScript the drawer renders open in flow, so the nav stays reachable. */
(() => {
  'use strict';
  const drawer = document.getElementById('nav-drawer');
  const openButton = document.getElementById('drawer-open');
  const closeButton = document.getElementById('drawer-close');
  if (!drawer || !openButton) return;
  const desktop = window.matchMedia('(min-width: 1024px)');
  let openedByButton = false;

  function syncInitial() {
    if (drawer.open && !openedByButton) {
      // The template renders the drawer open for the no-JS case; close it once JS is active.
      openedByButton = false;
      drawer.close();
    }
    openButton.setAttribute('aria-expanded', String(drawer.open && openedByButton));
  }

  function open() {
    if (desktop.matches || drawer.open) return;
    openedByButton = true;
    drawer.showModal();
    openButton.setAttribute('aria-expanded', 'true');
    document.body.classList.add('drawer-open');
  }

  function close({ restoreFocus = true } = {}) {
    if (!drawer.open) return;
    openedByButton = openedByButton && restoreFocus;
    drawer.close();
    if (restoreFocus && openedByButton) openButton.focus();
  }

  openButton.addEventListener('click', open);
  if (closeButton) closeButton.addEventListener('click', () => close());
  drawer.addEventListener('close', () => {
    openButton.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('drawer-open');
    if (openedByButton) { openButton.focus(); openedByButton = false; }
  });
  // Click on the backdrop area closes the modal drawer.
  drawer.addEventListener('click', event => { if (event.target === drawer) close(); });
  drawer.addEventListener('click', event => {
    if (event.target.closest('a') && drawer.open) close({ restoreFocus: false });
  });
  desktop.addEventListener('change', () => { if (desktop.matches) close({ restoreFocus: false }); });
  syncInitial();
})();

/* PH-026 link tabs: anchors with role=tab, server-rendered panels.
   JS adds roving tabindex, auto activation (Arrow/Left/Right/Home/End) and
   history-driven switching; without JS the links re-render the target panel.
   Tab ids look like dash-tab-overview / project-tab-overview, panels like
   dash-panel-overview / project-panel-overview and the ?tab= deep-link value
   is the shared suffix (overview). */
(() => {
  'use strict';
  document.querySelectorAll('[data-link-tabs]').forEach(tablist => {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    if (!tabs.length) return;
    const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
    if (panels.some(panel => !panel)) return;
    const suffix = tab => tab.id.split('-').pop();

    function currentId() {
      const requested = new URLSearchParams(window.location.search).get('tab');
      if (tabs.some(tab => suffix(tab) === requested)) return requested;
      // No (valid) URL state: the server-rendered aria-selected tab is authoritative,
      // so legacy entry points that render a non-first default tab stay in sync.
      const serverSelected = tabs.find(tab => tab.getAttribute('aria-selected') === 'true');
      return suffix(serverSelected || tabs[0]);
    }

    function activate(id, { focusTab = false, updateUrl = false } = {}) {
      tabs.forEach((tab, index) => {
        const selected = suffix(tab) === id;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        panels[index].hidden = !selected;
        if (selected) {
          tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          if (focusTab) tab.focus();
        }
      });
      if (updateUrl) {
        const url = new URL(window.location.href);
        url.searchParams.set('tab', id);
        window.history.pushState(null, '', url);
      }
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => {
        event.preventDefault();
        activate(suffix(tab), { updateUrl: true });
      });
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault();
        activate(suffix(tabs[next]), { focusTab: true, updateUrl: true });
      });
    });

    window.addEventListener('popstate', () => activate(currentId(), { focusTab: true }));
    activate(currentId());
  });
})();

/* Q-35: a failed weekly submission keeps entered data and moves focus to the error summary. */
(() => {
  'use strict';
  const summary = document.getElementById('week-error-summary');
  if (!summary) return;
  summary.setAttribute('tabindex', '-1');
  summary.focus();
})();


/* Q-40: first-run wizard forms keep the submit button busy to prevent double submit. */
(() => {
  'use strict';
  document.querySelectorAll('form[data-submit-once]').forEach(form => {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = '处理中…';
      form.setAttribute('aria-busy', 'true');
    });
  });
})();
