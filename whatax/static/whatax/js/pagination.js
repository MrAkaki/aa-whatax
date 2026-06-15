/* Client-side table pagination for Whale Tax.
 *
 * Any <table class="whatax-paginate"> with more than PAGE_SIZE body rows gets a
 * Bootstrap pager; tables at or below the threshold are left untouched (so the
 * control only appears when it's actually needed). Pagination is purely visual —
 * it shows/hides rows already rendered by the server, so sorting, links and forms
 * inside rows keep working.
 */
(function () {
  "use strict";

  var PAGE_SIZE = 20;

  function buildPager(table, rows) {
    var pageCount = Math.ceil(rows.length / PAGE_SIZE);
    var current = 1;

    // sort.js reorders the DOM rows then fires "whatax:sorted"; re-read the
    // tbody so the pager reflects the new order, and jump back to page 1.
    table.addEventListener("whatax:sorted", function () {
      rows = Array.prototype.slice.call(table.tBodies[0].rows);
      pageCount = Math.ceil(rows.length / PAGE_SIZE);
      showPage(1);
    });

    var nav = document.createElement("nav");
    nav.className = "whatax-pagination d-flex justify-content-between align-items-center mt-2 flex-wrap gap-2";

    var info = document.createElement("small");
    info.className = "text-muted";

    var ul = document.createElement("ul");
    ul.className = "pagination pagination-sm mb-0";

    function makeItem(label, page, opts) {
      opts = opts || {};
      var li = document.createElement("li");
      li.className = "page-item" + (opts.disabled ? " disabled" : "") + (opts.active ? " active" : "");
      var a = document.createElement("a");
      a.className = "page-link";
      a.href = "#";
      a.innerHTML = label;
      a.addEventListener("click", function (e) {
        e.preventDefault();
        if (opts.disabled || opts.active) return;
        showPage(page);
      });
      li.appendChild(a);
      return li;
    }

    function renderPager() {
      ul.innerHTML = "";
      ul.appendChild(makeItem("&laquo;", current - 1, { disabled: current === 1 }));

      // Windowed page numbers: first, last, and a span around the current page.
      var pages = [];
      for (var p = 1; p <= pageCount; p++) {
        if (p === 1 || p === pageCount || (p >= current - 2 && p <= current + 2)) {
          pages.push(p);
        } else if (pages[pages.length - 1] !== "…") {
          pages.push("…");
        }
      }
      pages.forEach(function (p) {
        if (p === "…") {
          ul.appendChild(makeItem("…", null, { disabled: true }));
        } else {
          ul.appendChild(makeItem(String(p), p, { active: p === current }));
        }
      });

      ul.appendChild(makeItem("&raquo;", current + 1, { disabled: current === pageCount }));

      var first = (current - 1) * PAGE_SIZE + 1;
      var last = Math.min(current * PAGE_SIZE, rows.length);
      info.textContent = first + "–" + last + " of " + rows.length;
    }

    function showPage(page) {
      current = Math.min(Math.max(page, 1), pageCount);
      var start = (current - 1) * PAGE_SIZE;
      var end = start + PAGE_SIZE;
      rows.forEach(function (row, i) {
        row.style.display = i >= start && i < end ? "" : "none";
      });
      renderPager();
    }

    nav.appendChild(info);
    nav.appendChild(ul);
    table.insertAdjacentElement("afterend", nav);
    showPage(1);
  }

  function init() {
    var tables = document.querySelectorAll("table.whatax-paginate");
    tables.forEach(function (table) {
      var tbody = table.tBodies[0];
      if (!tbody) return;
      var rows = Array.prototype.slice.call(tbody.rows);
      if (rows.length > PAGE_SIZE) {
        buildPager(table, rows);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
