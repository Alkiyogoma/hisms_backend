/* Lesson plan viewer. Shows a plan's attached files (PDFs, and Word /
   PowerPoint / Excel files converted to PDF on the server) in one PDF viewer
   with page navigation and zoom, beside the plan's comment thread.

   LPViewer.open(planId[, attachmentId[, 'thread']]) — markup in _lp_viewer.html. */
(function () {
  'use strict';

  var PDFJS = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/';
  var root, state = {};

  function $(sel) { return root.querySelector(sel); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function threadUrl(id) { return root.dataset.threadUrl.replace('/0/', '/' + id + '/'); }

  var pdfjsReady;
  function loadPdfJs() {
    if (window.pdfjsLib) return Promise.resolve(window.pdfjsLib);
    if (pdfjsReady) return pdfjsReady;
    pdfjsReady = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = PDFJS + 'pdf.min.js';
      s.onload = function () {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.js';
        resolve(window.pdfjsLib);
      };
      s.onerror = function () { pdfjsReady = null; reject(new Error('viewer')); };
      document.head.appendChild(s);
    });
    return pdfjsReady;
  }

  // ── open / close ─────────────────────────────────────────────────────
  function open(planId, attachmentId, pane) {
    root = root || document.getElementById('lpViewer');
    if (!root) return;
    state = { planId: String(planId), token: {} };
    root.dataset.pane = 'doc';
    $('#lpvTitle').textContent = 'Loading…';
    $('#lpvSub').textContent = '';
    $('#lpvTabs').innerHTML = '';
    $('#lpvList').innerHTML = '';
    $('#lpvFeedback').hidden = true;
    message('spin', 'Loading the plan…');
    setSwitch(pane === 'thread' ? 'thread' : 'doc');
    root.classList.add('open');
    document.body.style.overflow = 'hidden';
    state.returnFocus = document.activeElement;
    $('.lpv-close').focus();

    fetch(threadUrl(planId), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        if (String(planId) !== state.planId) return;
        state.data = data;
        $('#lpvTitle').textContent = data.plan.label;
        $('#lpvSub').textContent = data.plan.teacher + ' · ' + data.plan.status;
        var fb = $('#lpvFeedback');
        fb.hidden = !data.plan.feedback;
        if (data.plan.feedback) { fb.querySelector('span').textContent = data.plan.feedback; }
        renderTabs(data.files, attachmentId);
        renderComments(data.comments);
        renderForm(data);
        if (pane === 'thread' && data.can_comment) $('#lpvForm textarea').focus();
      })
      .catch(function () { message('', 'Could not open this plan.', 'Check your connection and try again.'); });
  }

  function close() {
    if (!root) return;
    root.classList.remove('open');
    document.body.style.overflow = '';
    state.token = {};
    if (state.pdf) { try { state.pdf.destroy(); } catch (e) {} }
    $('#lpvPages').innerHTML = '';
    if (state.returnFocus && state.returnFocus.focus) state.returnFocus.focus();
    state = {};
  }

  function setSwitch(pane) {
    root.dataset.pane = pane;
    root.querySelectorAll('.lpv-switch button').forEach(function (b) {
      b.classList.toggle('on', b.dataset.pane === pane);
    });
  }

  // ── files ────────────────────────────────────────────────────────────
  function renderTabs(files, attachmentId) {
    var tabs = $('#lpvTabs');
    tabs.innerHTML = '';
    if (!files.length) {
      toolbar(false);
      message('', 'No file attached.', 'This plan has no attached file to preview.');
      return;
    }
    var start = 0;
    files.forEach(function (f, i) { if (String(f.id) === String(attachmentId)) start = i; });
    tabs.hidden = files.length < 2;
    files.forEach(function (f, i) {
      var t = el('button', 'lpv-tab', f.name);
      t.type = 'button';
      t.title = f.name;
      t.setAttribute('role', 'tab');
      t.onclick = function () { showFile(i); };
      tabs.appendChild(t);
    });
    showFile(start);
  }

  function showFile(i) {
    var f = state.data.files[i];
    state.file = f;
    root.querySelectorAll('.lpv-tab').forEach(function (t, j) {
      t.classList.toggle('on', i === j);
      t.setAttribute('aria-selected', i === j ? 'true' : 'false');
    });
    $('#lpvDownload').href = f.download_url;
    $('#lpvDownload').setAttribute('download', f.name);
    var token = state.token = {};
    if (state.pdf) { try { state.pdf.destroy(); } catch (e) {} state.pdf = null; }
    state.scale = null;
    state.page = 1;
    if (f.kind === 'pdf') return showPdf(f, token);
    toolbar(false);
    if (f.kind === 'image') {
      var img = el('img');
      img.alt = f.name;
      img.src = f.preview_url;
      img.style.maxWidth = '100%';
      var pages = $('#lpvPages');
      pages.innerHTML = '';
      pages.appendChild(img);
      return;
    }
    if (f.kind === 'text') {
      message('spin', 'Loading…');
      fetch(f.preview_url, { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (text) {
          if (token !== state.token) return;
          var pages = $('#lpvPages');
          pages.innerHTML = '';
          pages.appendChild(/\.csv$/i.test(f.name) ? csvTable(text) : el('pre', '', text));
        })
        .catch(function () { if (token === state.token) unavailable(f); });
      return;
    }
    unavailable(f, 'There is no preview for this type of file.');
  }

  function unavailable(f, why) {
    toolbar(false);
    var pages = $('#lpvPages');
    pages.innerHTML = '';
    var box = el('div', 'lpv-msg');
    box.appendChild(el('b', '', 'Preview not available'));
    box.appendChild(document.createTextNode((why || 'This file could not be shown here.') + ' '));
    var a = el('a', '', 'Download the file');
    a.href = f.download_url;
    a.setAttribute('download', f.name);
    a.style.cssText = 'color:var(--hodari-blue,#023E8A);font-weight:700';
    box.appendChild(a);
    box.appendChild(document.createTextNode(' to open it.'));
    pages.appendChild(box);
  }

  function message(kind, title, detail) {
    var pages = $('#lpvPages');
    pages.innerHTML = '';
    var box = el('div', 'lpv-msg');
    if (kind === 'spin') box.appendChild(el('div', 'lpv-spin'));
    box.appendChild(el('b', '', title));
    if (detail) box.appendChild(document.createTextNode(detail));
    pages.appendChild(box);
  }

  // ── PDF ──────────────────────────────────────────────────────────────
  function showPdf(f, token) {
    toolbar(false);
    var office = !/\.pdf$/i.test(f.name);
    message('spin', office ? 'Preparing the preview…' : 'Loading…',
            office ? 'Word and other office files are converted to PDF so they look exactly as written. The first view can take a few seconds.' : '');
    var stage = 'viewer';
    loadPdfJs()
      .then(function (pdfjsLib) {
        stage = 'file';
        return pdfjsLib.getDocument({ url: f.preview_url, withCredentials: true }).promise;
      })
      .then(function (pdf) {
        if (token !== state.token) { pdf.destroy(); return; }
        state.pdf = pdf;
        $('#lpvPageCount').textContent = pdf.numPages;
        $('#lpvPageInput').max = pdf.numPages;
        toolbar(true);
        return renderPages(token, true);
      })
      .catch(function () {
        if (token !== state.token) return;
        unavailable(f, stage === 'viewer' ? 'The viewer could not load — check your connection.'
          : office ? 'The file could not be converted for preview.' : 'This PDF could not be shown here.');
      });
  }

  function fitWidthScale(page) {
    var box = $('#lpvPages');
    var avail = Math.max(240, box.clientWidth - 34);
    var w = page.getViewport({ scale: 1 }).width;
    return Math.min(2, avail / w);
  }

  function renderPages(token, fit) {
    var pdf = state.pdf;
    var pages = $('#lpvPages');
    return pdf.getPage(1).then(function (first) {
      if (fit || !state.scale) state.scale = fitWidthScale(first);
      updateZoom();
      pages.innerHTML = '';
      var dpr = window.devicePixelRatio || 1;
      var chain = Promise.resolve();
      var holders = [];
      // Size every placeholder from page 1 so page navigation lands on the
      // right place before all pages have rendered.
      var vp1 = first.getViewport({ scale: state.scale });
      for (var n = 1; n <= pdf.numPages; n++) {
        var holder = el('div', 'lpv-page');
        holder.dataset.page = n;
        holder.style.width = Math.floor(vp1.width) + 'px';
        holder.style.height = Math.floor(vp1.height) + 'px';
        pages.appendChild(holder);
        holders.push(holder);
      }
      holders.forEach(function (holder, idx) {
        chain = chain.then(function () {
          if (token !== state.token) return;
          return pdf.getPage(idx + 1).then(function (page) {
            if (token !== state.token) return;
            var vp = page.getViewport({ scale: state.scale });
            var canvas = el('canvas');
            canvas.width = Math.floor(vp.width * dpr);
            canvas.height = Math.floor(vp.height * dpr);
            canvas.style.width = Math.floor(vp.width) + 'px';
            canvas.style.height = Math.floor(vp.height) + 'px';
            canvas.setAttribute('aria-label', 'Page ' + (idx + 1));
            holder.style.width = canvas.style.width;
            holder.style.height = canvas.style.height;
            holder.appendChild(canvas);
            return page.render({
              canvasContext: canvas.getContext('2d'),
              viewport: vp,
              transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null
            }).promise;
          });
        });
      });
      goTo(Math.min(state.page || 1, pdf.numPages), true);
      return chain;
    });
  }

  function toolbar(on) {
    root.querySelectorAll('[data-pdf-only]').forEach(function (n) { n.hidden = !on; });
  }

  function updateZoom() { $('#lpvZoom').textContent = Math.round(state.scale * 100) + '%'; }

  function zoom(factor) {
    if (!state.pdf) return;
    var next = Math.min(4, Math.max(0.3, state.scale * factor));
    if (Math.abs(next - state.scale) < 0.001) return;
    state.scale = next;
    state.token = {};
    renderPages(state.token, false);
  }

  function fit() {
    if (!state.pdf) return;
    state.token = {};
    renderPages(state.token, true);
  }

  function goTo(n, instant) {
    if (!state.pdf) return;
    n = Math.min(state.pdf.numPages, Math.max(1, n | 0));
    var target = root.querySelector('.lpv-page[data-page="' + n + '"]');
    state.page = n;
    syncPage();
    if (target) {
      var pages = $('#lpvPages');
      pages.scrollTo({ top: target.offsetTop - pages.offsetTop - 8, behavior: instant ? 'auto' : 'smooth' });
    }
  }

  function syncPage() {
    $('#lpvPageInput').value = state.page;
    $('#lpvPrev').disabled = state.page <= 1;
    $('#lpvNext').disabled = !state.pdf || state.page >= state.pdf.numPages;
  }

  function onScroll() {
    if (!state.pdf) return;
    var pages = $('#lpvPages');
    var mid = pages.scrollTop + pages.clientHeight / 3;
    var current = 1;
    root.querySelectorAll('.lpv-page').forEach(function (p) {
      if (p.offsetTop - pages.offsetTop <= mid) current = +p.dataset.page;
    });
    if (current !== state.page) { state.page = current; syncPage(); }
  }

  function csvTable(text) {
    var table = el('table');
    text.split(/\r?\n/).filter(function (l) { return l.trim(); }).forEach(function (line, i) {
      var tr = el('tr');
      parseCsv(line).forEach(function (cell) { tr.appendChild(el(i ? 'td' : 'th', '', cell)); });
      table.appendChild(tr);
    });
    return table;
  }

  function parseCsv(line) {
    var out = [], cur = '', q = false;
    for (var i = 0; i < line.length; i++) {
      var c = line[i];
      if (q) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') q = false;
        else cur += c;
      } else if (c === '"') q = true;
      else if (c === ',') { out.push(cur); cur = ''; }
      else cur += c;
    }
    out.push(cur);
    return out;
  }

  // ── comments ─────────────────────────────────────────────────────────
  function renderComments(comments) {
    var list = $('#lpvList');
    list.innerHTML = '';
    $('#lpvCount').textContent = comments.length ? '(' + comments.length + ')' : '';
    $('#lpvSwitchCount').textContent = comments.length ? ' (' + comments.length + ')' : '';
    if (!comments.length) {
      list.appendChild(el('div', 'lpv-empty', 'No comments yet.'));
      return;
    }
    comments.forEach(function (c) { list.appendChild(commentNode(c)); });
    // Newest last, in view once laid out.
    requestAnimationFrame(function () { list.scrollTop = list.scrollHeight; });
  }

  function commentNode(c) {
    var item = el('div', 'lpv-c' + (c.role === 'Teacher' ? ' teacher' : '') + (c.mine ? ' mine' : ''));
    item.appendChild(el('div', 'lpv-c-av', c.initials));
    var main = el('div', 'lpv-c-main');
    var meta = el('div', 'lpv-c-meta');
    meta.appendChild(el('b', '', c.author));
    meta.appendChild(el('span', 'lpv-c-role', c.role));
    var time = el('time', '', c.created_label);
    time.setAttribute('datetime', c.created_at);
    meta.appendChild(time);
    main.appendChild(meta);
    main.appendChild(el('div', 'lpv-c-body', c.body));
    item.appendChild(main);
    return item;
  }

  function renderForm(data) {
    var form = $('#lpvForm');
    form.hidden = !data.can_comment;
    $('#lpvReadonly').hidden = data.can_comment;
    form.action = data.comment_url;
    form.querySelector('textarea').value = '';
    $('#lpvErr').textContent = '';
  }

  function submitComment(e) {
    e.preventDefault();
    var form = e.target;
    var box = form.querySelector('textarea');
    var btn = form.querySelector('button[type=submit]');
    var err = $('#lpvErr');
    if (!box.value.trim()) { err.textContent = 'Write a comment first.'; box.focus(); return; }
    btn.disabled = true;
    err.textContent = '';
    var planId = state.planId;
    fetch(form.action, {
      method: 'POST', credentials: 'same-origin', body: new FormData(form),
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.j.error || 'Could not post the comment.');
        if (planId !== state.planId) return;
        state.data.comments.push(res.j.comment);
        renderComments(state.data.comments);
        box.value = '';
        bumpCount(planId, state.data.comments.length);
      })
      .catch(function (x) { err.textContent = x.message || 'Could not post the comment.'; })
      .then(function () { btn.disabled = false; });
  }

  function bumpCount(planId, n) {
    document.querySelectorAll('[data-lp-comments="' + planId + '"]').forEach(function (b) {
      var c = b.querySelector('.lp-cmt-count');
      if (!c) { c = el('span', 'lp-cmt-count'); b.appendChild(c); }
      c.textContent = n;
    });
  }

  // ── wiring ───────────────────────────────────────────────────────────
  function init() {
    root = document.getElementById('lpViewer');
    if (!root) return;
    root.addEventListener('click', function (e) { if (e.target === root) close(); });
    $('.lpv-close').onclick = close;
    $('#lpvPrev').onclick = function () { goTo(state.page - 1); };
    $('#lpvNext').onclick = function () { goTo(state.page + 1); };
    $('#lpvPageInput').onchange = function () { goTo(+this.value); };
    $('#lpvZoomIn').onclick = function () { zoom(1.2); };
    $('#lpvZoomOut').onclick = function () { zoom(1 / 1.2); };
    $('#lpvFit').onclick = fit;
    $('#lpvPages').addEventListener('scroll', onScroll, { passive: true });
    $('#lpvForm').addEventListener('submit', submitComment);
    $('#lpvForm textarea').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('#lpvForm').requestSubmit(); }
    });
    root.querySelectorAll('.lpv-switch button').forEach(function (b) {
      b.onclick = function () { setSwitch(b.dataset.pane); };
    });
    document.addEventListener('keydown', function (e) {
      if (!root.classList.contains('open')) return;
      if (e.key === 'Escape') { e.stopImmediatePropagation(); close(); return; }
      var typing = /^(TEXTAREA|INPUT)$/.test((e.target || {}).tagName);
      if (typing || !state.pdf) return;
      if (e.key === 'PageDown' || e.key === 'ArrowRight') { e.preventDefault(); goTo(state.page + 1); }
      if (e.key === 'PageUp' || e.key === 'ArrowLeft') { e.preventDefault(); goTo(state.page - 1); }
      if ((e.key === '+' || e.key === '=') && !e.ctrlKey && !e.metaKey) zoom(1.2);
      if (e.key === '-' && !e.ctrlKey && !e.metaKey) zoom(1 / 1.2);
    }, true);
    window.addEventListener('resize', function () {
      if (state.pdf && root.classList.contains('open')) { clearTimeout(state.resizeT); state.resizeT = setTimeout(fit, 200); }
    });
    // Deep link from notifications: ?plan=<id>
    var m = /[?&]plan=(\d+)/.exec(window.location.search);
    if (m) open(m[1]);
  }

  window.LPViewer = { open: open, close: close };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
