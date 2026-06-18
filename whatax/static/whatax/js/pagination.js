/* Client-side table pagination for Whale Tax.
 *
 * Every <table class="whatax-paginate"> gets a controller that owns which body
 * rows are visible. It is the single place that toggles row display so sorting
 * (sort.js, "whatax:sorted") and searching (search.js, "whatax:filtered") just
 * reorder rows or flag them out, then let this re-page.
 *
 * "Visible" rows are those not filtered out by search; when there are more than
 * PAGE_SIZE of them a Bootstrap pager appears and only the current page shows,
 * otherwise the pager hides and every matching row is shown. Pagination is
 * purely visual — rows are rendered by the server, so links and forms inside
 * them keep working. The "No records." placeholder row (a single cell spanning
 * the table) is shown only when nothing else matches.
 */
(function () {
  "use strict";

  var PAGE_SIZE = 20;

  function isPlaceholderRow(row) {
    var first = row.cells[0];
    return row.cells.length === 1 && first && first.colSpan > 1;
  }

  function setup(table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var nav = document.createElement("nav");
    nav.className = "whatax-pagination d-flex justify-content-between align-items-center mt-2 flex-wrap gap-2";

    var info = document.createElement("small");
    info.className = "text-muted";

    var ul = document.createElement("ul");
    ul.className = "pagination pagination-sm mb-0";

    nav.appendChild(info);
    nav.appendChild(ul);
    table.insertAdjacentElement("afterend", nav);

    var matched = [];
    var placeholders = [];
    var pageCount = 1;
    var current = 1;

    // Re-read the DOM into the "matching rows" (in current sort order) and the
    // placeholder rows; called whenever the rows are sorted or filtered.
    function refresh() {
      matched = [];
      placeholders = [];
      Array.prototype.slice.call(tbody.rows).forEach(function (row) {
        if (isPlaceholderRow(row)) {
          placeholders.push(row);
        } else if (row.dataset.whataxFiltered !== "out") {
          matched.push(row);
        } else {
          row.style.display = "none";
        }
      });
      pageCount = Math.max(1, Math.ceil(matched.length / PAGE_SIZE));
      showPage(1);
    }

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

      var first = matched.length ? (current - 1) * PAGE_SIZE + 1 : 0;
      var last = Math.min(current * PAGE_SIZE, matched.length);
      info.textContent = first + "–" + last + " of " + matched.length;
    }

    function showPage(page) {
      current = Math.min(Math.max(page, 1), pageCount);

      // The pager only appears once there's more than one page of matches.
      var paged = matched.length > PAGE_SIZE;
      nav.style.display = paged ? "" : "none";

      var start = paged ? (current - 1) * PAGE_SIZE : 0;
      var end = paged ? start + PAGE_SIZE : matched.length;
      matched.forEach(function (row, i) {
        row.style.display = i >= start && i < end ? "" : "none";
      });

      // Show the empty-state placeholder only when nothing else is visible.
      placeholders.forEach(function (row) {
        row.style.display = matched.length === 0 ? "" : "none";
      });

      if (paged) renderPager();
    }

    table.addEventListener("whatax:sorted", refresh);
    table.addEventListener("whatax:filtered", refresh);
    refresh();
  }

  function init() {
    document.querySelectorAll("table.whatax-paginate").forEach(setup);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
