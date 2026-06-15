/* Click-to-sort for Whale Tax data tables.
 *
 * Every <table class="whatax-paginate"> gets sortable headers: clicking a
 * non-empty <th> reorders the tbody rows by that column, toggling
 * ascending/descending on repeated clicks. Numeric columns (after stripping
 * thousands separators) sort numerically; everything else sorts as
 * case-insensitive strings.
 *
 * After reordering the DOM, the table fires a "whatax:sorted" event so
 * pagination.js can re-read its cached row order and re-page from page 1.
 */
(function () {
  "use strict";

  var DASH = "—";

  // Parse a cell's text into a number, or null if it isn't numeric.
  // Strips commas, whitespace and any non-numeric prefix/suffix (units,
  // currency symbols). Empty cells and a lone dash are treated as "not a
  // value" by returning null so they can be sorted to the end.
  function parseNumber(text) {
    var t = (text || "").trim();
    if (t === "" || t === DASH) return null;
    var cleaned = t.replace(/,/g, "");
    // Pull out the first signed decimal number found in the string.
    var match = cleaned.match(/-?\d+(\.\d+)?/);
    if (!match) return null;
    var n = parseFloat(match[0]);
    return isNaN(n) ? null : n;
  }

  // Rows whose first cell spans the whole table (the "No records." empty
  // placeholders) shouldn't participate in sorting — keep them put.
  function isPlaceholderRow(row, columnCount) {
    if (row.cells.length >= columnCount) return false;
    var firstCell = row.cells[0];
    return firstCell && firstCell.colSpan > 1;
  }

  function cellText(row, index) {
    var cell = row.cells[index];
    return cell ? cell.textContent.trim() : "";
  }

  function sortTable(table, colIndex, ascending) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var columnCount = table.tHead && table.tHead.rows[0]
      ? table.tHead.rows[0].cells.length
      : 0;

    var allRows = Array.prototype.slice.call(tbody.rows);
    var sortable = [];
    var placeholders = [];
    allRows.forEach(function (row) {
      if (isPlaceholderRow(row, columnCount)) {
        placeholders.push(row);
      } else {
        sortable.push(row);
      }
    });

    // Decide numeric vs text: numeric only if every non-empty cell parses.
    var numeric = true;
    var sawValue = false;
    for (var i = 0; i < sortable.length; i++) {
      var raw = cellText(sortable[i], colIndex);
      if (raw === "" || raw === DASH) continue;
      sawValue = true;
      if (parseNumber(raw) === null) {
        numeric = false;
        break;
      }
    }
    if (!sawValue) numeric = false;

    var dir = ascending ? 1 : -1;

    sortable.sort(function (a, b) {
      var ta = cellText(a, colIndex);
      var tb = cellText(b, colIndex);

      if (numeric) {
        var na = parseNumber(ta);
        var nb = parseNumber(tb);
        // Missing values always sort to the end regardless of direction.
        if (na === null && nb === null) return 0;
        if (na === null) return 1;
        if (nb === null) return -1;
        return (na - nb) * dir;
      }

      // Empty / dash strings sort to the end regardless of direction.
      var ea = ta === "" || ta === DASH;
      var eb = tb === "" || tb === DASH;
      if (ea && eb) return 0;
      if (ea) return 1;
      if (eb) return -1;
      return ta.localeCompare(tb, undefined, { sensitivity: "base" }) * dir;
    });

    sortable.forEach(function (row) {
      tbody.appendChild(row);
    });
    placeholders.forEach(function (row) {
      tbody.appendChild(row);
    });

    table.dispatchEvent(new CustomEvent("whatax:sorted"));
  }

  function makeSortable(table) {
    if (!table.tHead || !table.tHead.rows[0]) return;
    var headerRow = table.tHead.rows[0];
    var headers = Array.prototype.slice.call(headerRow.cells);
    var state = { col: -1, ascending: true };

    headers.forEach(function (th, index) {
      if (th.textContent.trim() === "") return; // action/blank column

      th.classList.add("whatax-sortable");
      th.style.cursor = "pointer";

      th.addEventListener("click", function () {
        if (state.col === index) {
          state.ascending = !state.ascending;
        } else {
          state.col = index;
          state.ascending = true;
        }

        // Clear indicators on all headers, set on the active one.
        headers.forEach(function (h) {
          h.classList.remove("whatax-sort-asc", "whatax-sort-desc");
        });
        th.classList.add(state.ascending ? "whatax-sort-asc" : "whatax-sort-desc");

        sortTable(table, index, state.ascending);
      });
    });
  }

  function init() {
    var tables = document.querySelectorAll("table.whatax-paginate");
    tables.forEach(makeSortable);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
