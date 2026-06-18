/* Client-side search box for Whale Tax data tables.
 *
 * Every <table class="whatax-paginate"> gets a search input rendered above it.
 * Typing filters the tbody rows to those that match, then fires a
 * "whatax:filtered" event so pagination.js re-pages the surviving rows. Like
 * sorting and pagination, this is purely visual — it shows/hides rows already
 * rendered by the server, so links and forms inside rows keep working.
 *
 * What a query matches depends on the table's columns:
 *   - if any header is a name column (Structure / Player / Character / Miner /
 *     Payer / Payee) we match only those columns, so a structure table searches
 *     by structure name and a player table by player name — not by amounts,
 *     dates or systems that happen to share the typed text;
 *   - otherwise (no name column) we fall back to matching the whole row.
 * A row's optional data-search attribute is always included in the match text:
 * the staff/period record tables put a player's *other* character names there so
 * a player row surfaces when one of their characters matches the query (the main
 * character name is already shown in the Player cell).
 */
(function () {
  "use strict";

  // Header labels that identify a row by name; matched case-insensitively.
  var NAME_HEADERS = {
    structure: true,
    player: true,
    character: true,
    miner: true,
    payer: true,
    payee: true,
  };

  // A "No records." style placeholder row spans the table with a single cell;
  // it never participates in searching (pagination.js shows it when nothing
  // else matches).
  function isPlaceholderRow(row) {
    var first = row.cells[0];
    return row.cells.length === 1 && first && first.colSpan > 1;
  }

  // Column indexes to search: the name columns if the table has any, else [].
  // An empty list means "match the whole row".
  function nameColumns(table) {
    if (!table.tHead || !table.tHead.rows[0]) return [];
    var cells = table.tHead.rows[0].cells;
    var cols = [];
    for (var i = 0; i < cells.length; i++) {
      var label = cells[i].textContent.trim().toLowerCase();
      if (NAME_HEADERS[label]) cols.push(i);
    }
    return cols;
  }

  function rowText(row, cols) {
    var parts = [];
    if (cols.length) {
      cols.forEach(function (i) {
        var cell = row.cells[i];
        if (cell) parts.push(cell.textContent);
      });
    } else {
      parts.push(row.textContent);
    }
    if (row.dataset.search) parts.push(row.dataset.search);
    return parts.join(" ").toLowerCase();
  }

  function buildInput(table) {
    var wrap = document.createElement("div");
    wrap.className = "whatax-search input-group input-group-sm mb-2";

    var icon = document.createElement("span");
    icon.className = "input-group-text";
    icon.innerHTML = '<i class="fas fa-search"></i>';

    var input = document.createElement("input");
    input.type = "search";
    input.className = "form-control";
    input.placeholder = "Search…";
    input.setAttribute("aria-label", "Search table");

    wrap.appendChild(icon);
    wrap.appendChild(input);
    table.insertAdjacentElement("beforebegin", wrap);
    return input;
  }

  function makeSearchable(table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var cols = nameColumns(table);
    var input = buildInput(table);

    input.addEventListener("input", function () {
      var query = input.value.trim().toLowerCase();
      Array.prototype.slice.call(tbody.rows).forEach(function (row) {
        if (isPlaceholderRow(row)) return;
        if (query === "" || rowText(row, cols).indexOf(query) !== -1) {
          delete row.dataset.whataxFiltered;
        } else {
          row.dataset.whataxFiltered = "out";
        }
      });
      table.dispatchEvent(new CustomEvent("whatax:filtered"));
    });
  }

  function init() {
    var tables = document.querySelectorAll("table.whatax-paginate");
    tables.forEach(makeSearchable);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
