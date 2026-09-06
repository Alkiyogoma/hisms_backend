/* ══════════════════════════════════════════════════════════════════════════
   HODARI UI  ·  hodari-ui.js
   Modal + drawer behaviour for the Hodari component kit. No dependencies.
   Works with static (inline) modals and htmx-loaded dynamic content.

   OPEN / CLOSE via data attributes (no inline JS needed):
     <button data-hui-open="myModal">Edit</button>
     <div class="modal" id="myModal"> … <button data-hui-close>Cancel</button> </div>

   The same attributes work for drawers (class="drawer").

   DYNAMIC (htmx) modals — fetch a form/detail from the server into a modal:
     <button hx-get="/finance/invoice/5/edit/"
             hx-target="#hui-modal-root" hx-swap="innerHTML">Edit</button>
   The server returns a <c-modal> fragment; it auto-opens on swap.
   Add hx-target="#hui-drawer-root" to load into a drawer instead.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  var openEls = [];

  function lockScroll(on) {
    document.body.classList.toggle('hui-lock', on);
  }

  function open(el) {
    if (!el || el.classList.contains('is-open')) return;
    el.classList.add('is-open');
    openEls.push(el);
    lockScroll(true);
    // focus first focusable field for keyboard users
    var f = el.querySelector('input,select,textarea,button,[href],[tabindex]:not([tabindex="-1"])');
    if (f) { try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); } }
    el.dispatchEvent(new CustomEvent('hui:open', { bubbles: true }));
  }

  function close(el) {
    if (!el) return;
    el.classList.remove('is-open');
    openEls = openEls.filter(function (x) { return x !== el; });
    if (!openEls.length) lockScroll(false);
    el.dispatchEvent(new CustomEvent('hui:close', { bubbles: true }));
    // clear dynamic roots so :empty hides them and stale content is dropped
    if (el.parentElement && (el.parentElement.id === 'hui-modal-root' || el.parentElement.id === 'hui-drawer-root')) {
      setTimeout(function () { if (!el.classList.contains('is-open')) el.remove(); }, 260);
    }
  }

  function closeTop() {
    if (openEls.length) close(openEls[openEls.length - 1]);
  }

  // Expose a tiny programmatic API
  window.HodariUI = {
    open: function (id) { open(document.getElementById(id)); },
    close: function (id) { close(id ? document.getElementById(id) : null); },
    closeTop: closeTop
  };

  // ── Delegated click handling ──
  document.addEventListener('click', function (e) {
    var opener = e.target.closest('[data-hui-open]');
    if (opener) {
      e.preventDefault();
      open(document.getElementById(opener.getAttribute('data-hui-open')));
      return;
    }
    var closer = e.target.closest('[data-hui-close]');
    if (closer) {
      e.preventDefault();
      close(closer.closest('.modal, .drawer-scrim'));
      return;
    }
    // backdrop click (clicking the padding area / scrim, outside the panel)
    if (e.target.classList && (e.target.classList.contains('modal') || e.target.classList.contains('drawer-scrim'))) {
      close(e.target);
    }
  });

  // ── ESC closes the top-most overlay ──
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeTop();
  });

  // ── htmx: auto-open a modal/drawer swapped into a root, and close on success ──
  document.body.addEventListener('htmx:afterSwap', function (e) {
    var t = e.target;
    if (t && (t.id === 'hui-modal-root' || t.id === 'hui-drawer-root')) {
      var panel = t.querySelector('.modal, .drawer-scrim');
      if (panel) requestAnimationFrame(function () { open(panel); });
    }
  });
  // Server can trigger `HX-Trigger: hui:closeModal` after a successful save
  document.body.addEventListener('hui:closeModal', function () { closeTop(); });
})();
