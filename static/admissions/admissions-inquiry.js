/**
 * Admissions New Inquiry Page — channel selection, parent lookup,
 * sibling detection, grade cascade, and inline validation.
 * Extracted from inline <script> in new_inquiry.html.
 */
(function () {
  "use strict";

  /* ===========================================================
     S1 - Channel-first: form fields hidden until channel selected
     =========================================================== */
  var channelSelected = false;
  var formWrapper = document.getElementById("form-fields-wrapper");
  var channelBanner = document.getElementById("channel-required-banner");

  /* selectChannel is defined in the inline script in new_inquiry.html
     and assigned to window.selectChannel. Re-use it here. */
  var _selectChannel = window.selectChannel || function (el) {
    document.querySelectorAll(".channel-pill").forEach(function (p) {
      p.classList.remove("selected");
    });
    el.classList.add("selected");
    var val = el.getAttribute("data-value");
    var hiddenInput = document.getElementById("id_inquiry_channel");
    if (hiddenInput) hiddenInput.value = val;
    if (!channelSelected) {
      channelSelected = true;
      if (formWrapper) {
        formWrapper.style.display = "block";
        formWrapper.style.animation = "fadeIn 0.3s ease";
      }
      if (channelBanner) channelBanner.style.display = "none";
    }
  };
  if (!window.selectChannel) window.selectChannel = _selectChannel;

  /* ===========================================================
     Main init
     =========================================================== */
  document.addEventListener("DOMContentLoaded", function () {
    var phoneInput = document.getElementById("id_parent_phone");
    var nameInput = document.getElementById("id_parent_full_name");
    var emailInput = document.getElementById("id_parent_email");
    var invoiceInput = document.getElementById("id_parent_invoice_name");
    var siblingPanel = document.getElementById("sibling-panel");
    var noMatchPanel = document.getElementById("no-match-panel");
    var siblingList = document.getElementById("sibling-list");
    var matchedParentIdInput = document.getElementById(
      "id_sibling_matched_parent"
    );
    var sidebarWrapper = document.getElementById("sidebar-wrapper");
    var inquiryGrid = document.getElementById("inquiry-grid");

    /* ---- S4: Shared sibling-match display function ---- */
    function showSiblingMatch(data) {
      if (data.found) {
        if (nameInput && !nameInput.value)
          nameInput.value = data.guardian.full_name;
        if (emailInput && !emailInput.value)
          emailInput.value = data.guardian.email;
        if (invoiceInput && !invoiceInput.value)
          invoiceInput.value = data.guardian.preferred_invoice_name;
        if (matchedParentIdInput)
          matchedParentIdInput.value = data.guardian.id;

        var headerText =
          "Possible match found \u2014 " + data.guardian.full_name;
        if (data.siblings.length > 0) {
          headerText +=
            ", linked to " +
            data.siblings[0].full_name +
            ", " +
            data.siblings[0].class_name;
        }
        var headerHtml =
          '<div style="padding:8px 12px;background:rgba(2,58,165,.04);border-radius:6px;margin-bottom:8px;font-size:12.5px;color:#023AA5;font-weight:600">' +
          headerText +
          "</div>";
        siblingList.innerHTML = headerHtml;
        data.siblings.forEach(function (s) {
          var item = document.createElement("div");
          item.className = "trow";
          item.style.borderBottom = "1px solid var(--border)";
          item.innerHTML =
            '<div style="padding:8px 0;">' +
            '<div style="font-size:13px;font-weight:600;color:var(--text-primary);">' +
            s.full_name +
            "</div>" +
            '<div style="font-size:11px;color:var(--text-muted);">' +
            s.class_name +
            " &middot; " +
            s.admission_no +
            "</div>" +
            "</div>";
          siblingList.appendChild(item);
        });
        siblingPanel.style.display = "block";
        siblingPanel.style.opacity = "1";
        siblingPanel.style.transform = "none";
        noMatchPanel.style.display = "none";
        if (sidebarWrapper) sidebarWrapper.style.display = "block";
        if (inquiryGrid) { inquiryGrid.style.display = "grid"; inquiryGrid.className = "grid-8-4"; }
      } else {
        siblingPanel.style.display = "none";
        noMatchPanel.style.display = "block";
        if (matchedParentIdInput) matchedParentIdInput.value = "";
        if (sidebarWrapper) sidebarWrapper.style.display = "none";
        if (inquiryGrid) { inquiryGrid.style.display = "block"; inquiryGrid.className = ""; }
      }
    }

    /* ---- Phone lookup ---- */
    function doPhoneLookup() {
      var phone = phoneInput.value.trim();
      if (phone.length >= 8) {
        fetch(
          "/admissions/api/parent-lookup/?phone=" +
            encodeURIComponent(phone)
        )
          .then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          })
          .then(showSiblingMatch)
          .catch(function (err) {
            console.error("Lookup error:", err);
          });
      }
    }

    var lookupTimeout = null;
    if (phoneInput) {
      phoneInput.addEventListener("input", function () {
        clearTimeout(lookupTimeout);
        if (this.value.trim().length >= 8) {
          lookupTimeout = setTimeout(doPhoneLookup, 500);
        }
      });
      phoneInput.addEventListener("blur", doPhoneLookup);
    }

    /* ---- S4: Parent name typeahead + sibling match ---- */
    if (nameInput) {
      var nameDropdown = document.getElementById("parent-name-dropdown");
      var nameSearchTimeout = null;
      var activeNameIndex = -1;

      function renderNameResults(results) {
        nameDropdown.innerHTML = "";
        activeNameIndex = -1;
        if (!results || results.length === 0) {
          nameDropdown.classList.remove("open");
          return;
        }
        results.forEach(function (g, idx) {
          var item = document.createElement("div");
          item.className = "parent-name-item";
          item.setAttribute("data-index", idx);
          var detail = g.phone ? g.phone : "";
          if (g.email)
            detail += (detail ? " \u00b7 " : "") + g.email;
          var sibs = "";
          if (g.siblings && g.siblings.length) {
            var names = g.siblings
              .map(function (s) {
                return s.full_name + " (" + s.class_name + ")";
              })
              .join(", ");
            sibs =
              "Sibling" +
              (g.siblings.length > 1 ? "s" : "") +
              ": " +
              names;
          }
          var nameEl = document.createElement("div");
          nameEl.className = "pni-name";
          nameEl.textContent = g.full_name;
          item.appendChild(nameEl);
          if (detail) {
            var detEl = document.createElement("div");
            detEl.className = "pni-detail";
            detEl.textContent = detail;
            item.appendChild(detEl);
          }
          if (sibs) {
            var sibsEl = document.createElement("div");
            sibsEl.className = "pni-siblings";
            sibsEl.textContent = sibs;
            item.appendChild(sibsEl);
          }
          item.addEventListener("mousedown", function (e) {
            e.preventDefault();
            selectParentName(g);
          });
          nameDropdown.appendChild(item);
        });
        nameDropdown.classList.add("open");
      }

      function selectParentName(g) {
        nameInput.value = g.full_name;
        if (emailInput && !emailInput.value && g.email)
          emailInput.value = g.email;
        if (invoiceInput && !invoiceInput.value && g.preferred_invoice_name)
          invoiceInput.value = g.preferred_invoice_name;
        nameDropdown.classList.remove("open");
        if (matchedParentIdInput) matchedParentIdInput.value = g.id;
        if (g.siblings && g.siblings.length > 0) {
          showSiblingMatch({
            found: true,
            guardian: g,
            siblings: g.siblings,
          });
        }
      }

      function searchParentNames(q) {
        fetch(
          "/admissions/api/parent-name-search/?q=" + encodeURIComponent(q)
        )
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            renderNameResults(data.results);
          })
          .catch(function (err) {
            console.error("Name search error:", err);
          });
      }

      nameInput.addEventListener("input", function () {
        clearTimeout(nameSearchTimeout);
        var val = this.value.trim();
        if (val.length >= 2) {
          nameSearchTimeout = setTimeout(function () {
            searchParentNames(val);
          }, 300);
        } else {
          nameDropdown.classList.remove("open");
        }
      });

      nameInput.addEventListener("keydown", function (e) {
        var items = nameDropdown.querySelectorAll(".parent-name-item");
        if (!items.length) return;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          activeNameIndex = Math.min(activeNameIndex + 1, items.length - 1);
          items.forEach(function (it, i) {
            it.classList.toggle("active", i === activeNameIndex);
          });
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          activeNameIndex = Math.max(activeNameIndex - 1, 0);
          items.forEach(function (it, i) {
            it.classList.toggle("active", i === activeNameIndex);
          });
        } else if (e.key === "Enter" && activeNameIndex >= 0) {
          e.preventDefault();
          items[activeNameIndex].dispatchEvent(new Event("mousedown"));
        } else if (e.key === "Escape") {
          nameDropdown.classList.remove("open");
        }
      });

      nameInput.addEventListener("blur", function () {
        setTimeout(function () {
          nameDropdown.classList.remove("open");
        }, 200);
      });
    }
  });

  /* ---- S3 - Grade / department cascade with REAL capacity check */
  var ADMISSION_GRADES =
    window.ADMISSION_GRADES ||
    (function () {
      var el = document.getElementById("admission-grades-data");
      try { return el ? JSON.parse(el.textContent) : []; }
      catch (e) { return []; }
    })();

  function initGradeCascade() {
    var deptSelect = document.getElementById("id_applying_department");
    var gradeSelect = document.getElementById("id_grade_applying_for");
    var capacityIndicator = document.getElementById("capacity-indicator");
    if (!deptSelect || !gradeSelect) return;

    function rebuildGradeOptions() {
      var dept = deptSelect.value;
      var current = gradeSelect.value;
      gradeSelect.innerHTML = "";
      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Select grade / class";
      gradeSelect.appendChild(placeholder);
      ADMISSION_GRADES.filter(function (g) {
        return !dept || g.department === dept;
      }).forEach(function (g) {
        var opt = document.createElement("option");
        opt.value = g.name;
        opt.textContent = g.name;
        if (g.name === current) opt.selected = true;
        gradeSelect.appendChild(opt);
      });
      if (capacityIndicator && !gradeSelect.value) capacityIndicator.style.display = "none";
    }

    deptSelect.addEventListener("change", function () {
      gradeSelect.value = "";
      rebuildGradeOptions();
      if (capacityIndicator) capacityIndicator.style.display = "none";
    });
    rebuildGradeOptions();

    gradeSelect.addEventListener("change", function () {
      var grade = this.value;
      if (!grade) {
        capacityIndicator.style.display = "none";
        return;
      }
      capacityIndicator.style.display = "block";
      capacityIndicator.className = "";
      capacityIndicator.innerHTML =
        '<span style="color:var(--text-muted)">Checking capacity\u2026</span>';

      fetch(
        "/admissions/api/capacity-check/?grade=" + encodeURIComponent(grade)
      )
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (!data.found) {
            capacityIndicator.className = "capacity-ok";
            capacityIndicator.innerHTML =
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;vertical-align:text-bottom;"><path d="M20 6L9 17l-5-5"></path></svg>' +
              grade +
              " \u2014 no class record found. Grade will be saved as entered.";
            return;
          }
          if (data.is_full) {
            capacityIndicator.className = "capacity-warn";
            capacityIndicator.innerHTML =
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;vertical-align:text-bottom;"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>' +
              "<strong>" +
              grade +
              " is currently at full capacity</strong> (" +
              data.current_count +
              "/" +
              data.max_capacity +
              " students)." +
              " Would you like to discuss the waitlist with this parent?" +
              '<br><span style="font-size:11px;color:var(--text-muted);margin-top:4px;display:inline-block">The form can still be saved \u2014 the applicant may be placed on the waitlist.</span>';
          } else {
            capacityIndicator.className = "capacity-ok";
            capacityIndicator.innerHTML =
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;vertical-align:text-bottom;"><path d="M20 6L9 17l-5-5"></path></svg>' +
              grade +
              " has <strong>" +
              data.remaining +
              " space" +
              (data.remaining !== 1 ? "s" : "") +
              " available</strong> (" +
              data.current_count +
              "/" +
              data.max_capacity +
              ").";
          }
        })
        .catch(function () {
          capacityIndicator.className = "capacity-ok";
          capacityIndicator.innerHTML =
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;vertical-align:text-bottom;"><path d="M20 6L9 17l-5-5"></path></svg>' +
            grade +
            " selected.";
        });
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initGradeCascade);
  } else {
    initGradeCascade();
  }

  /* ===========================================================
     S5 - Inline validation: highlight missing mandatory fields
     =========================================================== */
  document
    .getElementById("inquiry-form")
    .addEventListener("submit", function (e) {
      var valid = true;
      var firstInvalid = null;

      var hiddenInput = document.getElementById("id_inquiry_channel");
      if (!hiddenInput || !hiddenInput.value) {
        var channelCard = document.getElementById("channel-card");
        if (channelCard) {
          channelCard.classList.add("channel-error-flash");
          setTimeout(function () {
            channelCard.classList.remove("channel-error-flash");
          }, 3000);
        }
        if (!firstInvalid) firstInvalid = channelCard;
        valid = false;
      }

      var requiredFields = [
        {
          el: document.getElementById("id_parent_full_name"),
          label: "Parent name",
        },
        { el: document.getElementById("id_parent_phone"), label: "Phone" },
        {
          el: document.getElementById("id_child_full_name"),
          label: "Student name",
        },
        {
          el: document.getElementById("id_child_date_of_birth"),
          label: "DOB",
        },
        {
          el: document.getElementById("id_grade_applying_for"),
          label: "Grade",
        },
        {
          el: document.getElementById("id_applying_department"),
          label: "Department",
        },
        {
          el: document.getElementById("id_parent_relationship"),
          label: "Relationship",
        },
      ];

      requiredFields.forEach(function (f) {
        if (!f.el) return;
        var val = f.el.value ? f.el.value.trim() : "";
        if (!val) {
          f.el.classList.add("field-error-highlight");
          if (!firstInvalid) firstInvalid = f.el;
          valid = false;
          var wrapper =
            f.el.closest(".form-group") || f.el.closest(".grid-2-item");
          if (wrapper) {
            var existingErr = wrapper.querySelector(".hf2-error");
            if (!existingErr) {
              var errP = document.createElement("p");
              errP.className = "hf2-error";
              errP.textContent = f.label + " is required.";
              wrapper.appendChild(errP);
            }
          }
        } else {
          f.el.classList.remove("field-error-highlight");
          var wrapper =
            f.el.closest(".form-group") || f.el.closest(".grid-2-item");
          if (wrapper) {
            var existingErr = wrapper.querySelector(".hf2-error");
            if (
              existingErr &&
              existingErr.textContent.indexOf("is required") >= 0
            )
              existingErr.remove();
          }
        }
      });

      if (!valid) {
        e.preventDefault();
        if (firstInvalid)
          firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    });
})();

/* ---- Dismiss sibling match panel ---- */
function dismissSiblingPanel() {
  var panel = document.getElementById("sibling-panel");
  if (panel) {
    panel.style.transition = "opacity 0.2s, transform 0.2s";
    panel.style.opacity = "0";
    panel.style.transform = "translateY(-8px)";
    setTimeout(function () {
      panel.style.display = "none";
    }, 200);
  }
  var cb = document.getElementById("link_to_existing");
  if (cb) cb.checked = false;
  var matchedParentIdInput = document.getElementById(
    "id_sibling_matched_parent"
  );
  if (matchedParentIdInput) matchedParentIdInput.value = "";
  var noMatchPanel = document.getElementById("no-match-panel");
  if (noMatchPanel) noMatchPanel.style.display = "block";
  var sidebarWrapper = document.getElementById("sidebar-wrapper");
  if (sidebarWrapper) sidebarWrapper.style.display = "none";
  var inquiryGrid = document.getElementById("inquiry-grid");
  if (inquiryGrid) { inquiryGrid.style.display = "block"; inquiryGrid.className = ""; }
}

/* ---- Previous School 'Other' toggle ---- */
(function () {
  var prevSelect = document.getElementById("id_previous_school");
  var otherWrap = document.getElementById("previous-school-other-wrap");
  if (prevSelect && otherWrap) {
    prevSelect.addEventListener("change", function () {
      otherWrap.style.display =
        this.value === "__other__" ? "block" : "none";
    });
  }
})();
