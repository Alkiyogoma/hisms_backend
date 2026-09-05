/**
 * Admissions Detail Page — HTMX post-request refresh logic.
 * After status-changing actions, reload the page so server-rendered
 * conditional sections ({% if status == '...' %}) re-render correctly.
 */
(function () {
  "use strict";

  var _statusChangingPaths = [
    "/transition/", "/decision/", "/revert/", "/enrol/",
    "/fee/confirm", "/fee/unconfirm",
    "/assessment/", "/hod-review",
    "/logistics/", "/documents"
  ];

  document.addEventListener("htmx:afterOnLoad", function (evt) {
    var rc = evt.detail.requestConfig || {};
    var path = rc.path || "";
    var verb = rc.verb || "get";

    // Skip GET loads (initial section population)
    if (verb.toLowerCase() === "get") return;

    // After any status-changing POST, reload the page so conditional
    // sections (assessment card, fee gate, HOD review, decision, enrolment,
    // revert) re-render at the correct stage.
    var isStatusChange = _statusChangingPaths.some(function (p) {
      return path.indexOf(p) !== -1;
    });
    if (isStatusChange) {
      // Brief delay so the toast is visible before reload
      setTimeout(function () { location.reload(); }, 400);
      return;
    }
  });
})();
