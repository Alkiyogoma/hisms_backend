/* Parent sign-in (phone or email + 4-digit code). Talks to users/views.py
   ParentOTPRequestView / ParentOTPVerifyView; the server checks every code. */
(function () {
  'use strict';
  var root = document.getElementById('parentSignin');
  if (!root) return;

  var step1 = document.getElementById('psStep1');
  var step2 = document.getElementById('psStep2');
  var contactInput = document.getElementById('ps-contact');
  var err1 = document.getElementById('ps-step1-error');
  var err2 = document.getElementById('ps-step2-error');
  var instr = document.getElementById('ps-instr');
  var digits = Array.prototype.slice.call(root.querySelectorAll('.ps-digit'));
  var resendBtn = document.getElementById('psResend');
  var timerEl = document.getElementById('ps-timer');
  var csrf = root.querySelector('input[name="csrfmiddlewaretoken"]').value;
  var contact = '';
  var timer = null;
  var busy = false;

  function show(el, kind, text) {
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.className = 'alert ps-alert alert-' + kind;
    el.textContent = text;           // server text is never parsed as HTML
    el.hidden = false;
  }

  function setBusy(btn, on) {
    busy = on;
    btn.disabled = on;
    btn.setAttribute('aria-busy', on ? 'true' : 'false');
    btn.querySelector('.btn-text').style.display = on ? 'none' : '';
    btn.querySelector('.btn-arrow').style.display = on ? 'none' : '';
    btn.querySelector('.spinner').style.display = on ? 'block' : 'none';
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Something went wrong. Please try again.' }; });
    });
  }

  function startTimer(seconds) {
    clearInterval(timer);
    var left = Math.max(0, parseInt(seconds, 10) || 45);
    resendBtn.disabled = true;
    function tick() {
      if (left <= 0) {
        clearInterval(timer);
        timerEl.textContent = "Didn't get it?";
        resendBtn.disabled = false;
        return;
      }
      timerEl.textContent = 'Resend in ' + left + 's';
      left -= 1;
    }
    tick();
    timer = setInterval(tick, 1000);
  }

  function clearDigits() { digits.forEach(function (d) { d.value = ''; d.removeAttribute('aria-invalid'); }); }
  function code() { return digits.map(function (d) { return d.value; }).join(''); }

  function devPreview(text) {
    var box = document.getElementById('ps-dev');
    if (!text) { box.hidden = true; return; }
    document.getElementById('ps-dev-msg').textContent = text;
    box.hidden = false;
  }

  function sent(data) {
    instr.textContent = data.message;
    devPreview(data.dev_preview);
    show(err2, '', '');
    clearDigits();
    startTimer(data.resend_after);
  }

  step1.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) return;
    var value = contactInput.value.trim();
    if (!value) {
      contactInput.setAttribute('aria-invalid', 'true');
      show(err1, 'error', 'Enter the phone number or email the school has on record.');
      contactInput.focus();
      return;
    }
    var btn = document.getElementById('psLogin');
    setBusy(btn, true);
    show(err1, '', '');
    post(root.dataset.requestUrl, { contact: value }).then(function (data) {
      setBusy(btn, false);
      if (!data.ok) {
        contactInput.setAttribute('aria-invalid', 'true');
        show(err1, 'error', data.error || 'We could not send a code. Please try again.');
        contactInput.focus();
        return;
      }
      contactInput.removeAttribute('aria-invalid');
      contact = value;
      step1.hidden = true;
      step2.hidden = false;
      sent(data);
      digits[0].focus();
    }).catch(function () {
      setBusy(btn, false);
      show(err1, 'error', 'Network error. Check your connection and try again.');
    });
  });

  contactInput.addEventListener('input', function () {
    contactInput.removeAttribute('aria-invalid');
    show(err1, '', '');
  });

  function fill(start, text) {
    var chars = (text || '').replace(/\D/g, '').split('');
    var i = start;
    chars.forEach(function (c) { if (i < digits.length) { digits[i].value = c; i += 1; } });
    var next = Math.min(i, digits.length - 1);
    digits[next].focus();
    if (code().length === digits.length) step2.requestSubmit();
  }

  digits.forEach(function (input, i) {
    input.addEventListener('input', function () {
      var v = input.value.replace(/\D/g, '');
      if (v.length > 1) { input.value = ''; fill(i, v); return; }   // autofill / fast typing
      input.value = v;
      show(err2, '', '');
      if (v && i < digits.length - 1) digits[i + 1].focus();
      if (code().length === digits.length) step2.requestSubmit();
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Backspace' && !input.value && i > 0) { digits[i - 1].focus(); digits[i - 1].value = ''; e.preventDefault(); }
      else if (e.key === 'ArrowLeft' && i > 0) { digits[i - 1].focus(); e.preventDefault(); }
      else if (e.key === 'ArrowRight' && i < digits.length - 1) { digits[i + 1].focus(); e.preventDefault(); }
    });
    input.addEventListener('paste', function (e) {
      var text = (e.clipboardData || window.clipboardData).getData('text');
      e.preventDefault();
      fill(i, text);
    });
    input.addEventListener('focus', function () { input.select(); });
  });

  step2.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy) return;
    var value = code();
    if (!/^\d{4}$/.test(value)) {
      show(err2, 'error', 'Enter the 4 digits we sent you.');
      var firstEmpty = digits.filter(function (d) { return !d.value; })[0] || digits[0];
      firstEmpty.focus();
      return;
    }
    var btn = document.getElementById('psVerify');
    setBusy(btn, true);
    var next = new URLSearchParams(window.location.search).get('next') || '';
    post(root.dataset.verifyUrl, { contact: contact, code: value, next: next }).then(function (data) {
      if (data.ok) {
        show(err2, 'success', 'Signing you in…');
        clearInterval(timer);
        window.location.assign(data.redirect);
        return;
      }
      setBusy(btn, false);
      show(err2, 'error', data.error || 'Sign-in failed. Please try again.');
      clearDigits();
      digits.forEach(function (d) { d.setAttribute('aria-invalid', 'true'); });
      digits[0].focus();
    }).catch(function () {
      setBusy(btn, false);
      show(err2, 'error', 'Network error. Check your connection and try again.');
    });
  });

  resendBtn.addEventListener('click', function () {
    resendBtn.disabled = true;
    post(root.dataset.requestUrl, { contact: contact }).then(function (data) {
      if (data.ok) {
        sent(data);
        show(err2, 'success', 'A new code has been sent. Earlier codes no longer work.');
        digits[0].focus();
      } else {
        show(err2, 'error', data.error || 'We could not send a new code.');
        // The server enforces the cooldown; let the parent try again after it.
        startTimer(data.code === 'cooldown' ? 45 : 5);
      }
    }).catch(function () {
      show(err2, 'error', 'Network error. Check your connection and try again.');
      resendBtn.disabled = false;
    });
  });

  document.getElementById('psChange').addEventListener('click', function () {
    clearInterval(timer);
    step2.hidden = true;
    step1.hidden = false;
    show(err1, '', '');
    devPreview('');
    contactInput.focus();
  });
})();
