/* ══════════════════════════════════════════════════════════════════════════
   HODARI PASSWORD TOGGLE  ·  password-toggle.js
   Adds a show/hide (eye) button to every password input on the page, however
   it was rendered (Django forms, hand-written inputs, htmx-swapped content).
   Self-contained: injects its own minimal CSS so it works on app-shell pages
   AND on the standalone auth pages that don't load the app stylesheets.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function injectStyles() {
    if (document.getElementById('hodari-pw-toggle-styles')) return;
    var css =
      '.pw-wrap{position:relative;display:block}' +
      '.pw-wrap>input{padding-right:42px !important}' +
      '.pw-toggle{position:absolute;top:0;right:0;height:100%;min-height:34px;width:40px;' +
      'display:flex;align-items:center;justify-content:center;background:transparent;border:none;' +
      'padding:0;margin:0;cursor:pointer;color:#8492A6;transition:color .12s;line-height:0}' +
      '.pw-toggle:hover{color:#023AA5}' +
      '.pw-toggle:focus-visible{outline:2px solid rgba(2,58,165,.4);outline-offset:-2px;border-radius:6px}' +
      '.pw-toggle svg{width:18px;height:18px;display:block}';
    var style = document.createElement('style');
    style.id = 'hodari-pw-toggle-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function enhance(input) {
    if (!input || input.dataset.pwToggle) return;
    // getAttribute so we only wrap fields that START as password (toggling to
    // "text" changes .type but the attribute check keeps re-scans idempotent).
    if ((input.getAttribute('type') || '').toLowerCase() !== 'password') return;
    input.dataset.pwToggle = '1';

    var wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    var parent = input.parentNode;
    if (!parent) return;
    parent.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-toggle';
    btn.tabIndex = -1; // don't trap tab order between fields
    btn.setAttribute('aria-label', 'Show password');
    btn.innerHTML = EYE;
    wrap.appendChild(btn);

    btn.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.innerHTML = show ? EYE_OFF : EYE;
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      var pos = input.value.length;
      input.focus();
      try { input.setSelectionRange(pos, pos); } catch (e) {}
    });
  }

  function scan(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll('input[type="password"]').forEach(enhance);
  }

  function init() {
    injectStyles();
    scan(document);
    // Re-scan content injected by htmx (modals, swapped forms).
    if (document.body) {
      document.body.addEventListener('htmx:afterSwap', function (e) { scan(e.target); });
      document.body.addEventListener('htmx:load', function (e) { scan(e.target); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose for manual re-scan if a page adds password inputs by other means.
  window.HodariPasswordToggle = scan;
})();
