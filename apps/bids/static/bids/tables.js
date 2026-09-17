// Sortable tables for the bids pages: click a header to sort, shift-click to add a secondary key; the sort is
// remembered per page (PCA.pref). Cells sort by data-v when present (numbers / ISO dates), else by text.
(function () {
  function val(td) { if (!td) return null; const v = td.dataset.v; if (v !== undefined && v !== '') { const n = Number(v); return isNaN(n) ? v : n; } return td.textContent.trim().toLowerCase(); }
  function cmp(a, b) { if (a === null || a === '') return 1; if (b === null || b === '') return -1; if (typeof a === 'number' && typeof b === 'number') return a - b; return String(a).localeCompare(String(b), undefined, {numeric: true}); }
  document.querySelectorAll('table[data-sortable]').forEach(table => {
    const key = 'sort.' + (table.id || 'table'), tbody = table.tBodies[0];
    let keys = PCA.pref(key, []);
    function apply() {
      if (!keys.length) return;
      const rows = [...tbody.rows].filter(r => !r.classList.contains('tot'));
      rows.sort((r1, r2) => { for (const [i, dir] of keys) { const c = cmp(val(r1.cells[i]), val(r2.cells[i])); if (c) return dir * c; } return 0; });
      rows.forEach(r => tbody.appendChild(r));
      table.querySelectorAll('th').forEach((th, i) => { th.classList.toggle('sorted', keys.some(k => k[0] === i)); const k = keys.find(k => k[0] === i); th.dataset.dir = k ? (k[1] > 0 ? 'asc' : 'desc') : ''; });
    }
    table.querySelectorAll('th').forEach((th, i) => {
      if (th.dataset.nosort !== undefined) return;
      th.style.cursor = 'pointer';
      th.addEventListener('click', e => {
        const cur = keys.find(k => k[0] === i);
        if (e.shiftKey) { if (cur) cur[1] = -cur[1]; else keys.push([i, th.dataset.numeric !== undefined ? -1 : 1]); }
        else keys = [[i, cur ? -cur[1] : (th.dataset.numeric !== undefined ? -1 : 1)]];
        PCA.setPref(key, keys); apply();
      });
    });
    apply();
  });
})();
