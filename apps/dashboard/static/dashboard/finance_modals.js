// Finance modals: the generic JSON table drill (PCADrill) shared by the finance pages, and the
// "what makes up total liabilities / assets" breakdowns (PCALedger). Chart.js + PCA.money come from base.html.
window.PCADrill = (function () {
  const fmt = v => v == null ? '—' : v.toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2});
  const esc = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const KEYS = ['kind', 'side', 'book', 'bucket', 'cust', 'doc_type', 'due_from', 'due_to', 'period', 'day', 'group', 'title', 'vendor', 'acct', 'sub', 'as_of'];
  let url = null, asOf = null;   // asOf: the snapshot day being viewed when it is not the latest (bind opts)
  function open(d) {
    const modal = document.getElementById('drillmodal');
    if (!modal || !url) return;
    // data-* attributes of a .drill element, or a programmatic call: open({title, q: {any: 'params'}})
    const q = new URLSearchParams(d.q || {});
    for (const k of KEYS) if (d[k]) q.set(k, d[k]);
    if (asOf && !d.noAsOf && !q.has('as_of')) q.set('as_of', asOf);
    document.getElementById('drilltitle').textContent = d.title || 'Detail';
    document.getElementById('drillsum').textContent = '';
    const note = document.getElementById('drillnote');   // server-side caveat for this drill (e.g. "today's documents, not Sep 2's")
    if (note) { note.hidden = true; note.textContent = ''; }
    const searchBox = document.getElementById('drillsearch');
    searchBox.value = '';
    const body = document.getElementById('drillbody');
    body.innerHTML = '<div class="empty">loading…</div>';
    // the drill can be opened from inside the liabilities modal (z 60) — keep it on top
    modal.style.zIndex = 70;
    modal.style.display = 'flex';
    setTimeout(() => searchBox.focus(), 50);
    fetch(url + '?' + q.toString()).then(r => r.json()).then(res => {
      document.getElementById('drillsum').textContent = res.summary || (res.n + ' rows · ' + fmt(res.total) + (res.truncated ? (res.trunc_note || ' · showing top 400 by size') : ''));
      if (note) { note.textContent = res.notice || ''; note.hidden = !res.notice; }
      // optional typed columns: pct_cols hold fractions (0.123 → 12.3%), pts_cols margin points (+3.2), num_cols plain counts
      const pctCols = res.pct_cols || [], ptsCols = res.pts_cols || [], numCols = res.num_cols || [];
      const isNum = k => res.money_cols.includes(k) || pctCols.includes(k) || ptsCols.includes(k) || numCols.includes(k) || ['days_past_due', 'n', 'pm_pct'].includes(k);
      // long free-text columns get a wide single-line cell with the full text on hover; everything else never wraps
      const isText = k => (res.text_cols || []).includes(k) || ['doc_desc', 'descr', 'tran_desc', 'comment', 'title', 'description'].includes(k);
      // money_digits: 0 → whole dollars (project-level figures); default keeps cents (ledger documents)
      const money = res.money_digits === 0 ? (v => v == null ? '—' : v.toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0})) : fmt;
      const links = res.link_map || {};
      let sortKey = null, sortDir = 1, filter = '';
      const cellClass = k => (isNum(k) ? 'num' : isText(k) ? 'trunc drill-text' : 'nowrap');
      const cmp = (a, b) => {
        const x = a[sortKey], y = b[sortKey];
        if (x == null || x === '') return y == null || y === '' ? 0 : 1;
        if (y == null || y === '') return -1;
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true, sensitivity: 'base'})) * sortDir;
      };
      const render = () => {
        const rows = sortKey ? res.rows.slice().sort(cmp) : res.rows;
        let html = '<table class="data dense drill-table"><thead><tr>' + res.cols.map(c =>
          '<th class="' + (isNum(c[0]) ? 'num ' : '') + 'drill-sort' + (sortKey === c[0] ? ' sorted' : '') + '" data-k="' + c[0] + '" title="click to sort">' + c[1] +
          (sortKey === c[0] ? (sortDir > 0 ? ' ▴' : ' ▾') : '') + '</th>').join('') + '</tr></thead><tbody>';
        let shown = 0;
        for (const row of rows) {
          const hay = filter ? res.cols.map(c => row[c[0]] == null ? '' : String(row[c[0]])).join(' ').toLowerCase() : '';
          if (filter && !hay.includes(filter)) continue;
          shown++;
          html += '<tr>' + res.cols.map(c => {
            let v = row[c[0]];
            if (res.money_cols.includes(c[0])) v = money(v);
            else if (v == null) v = '';
            else if (pctCols.includes(c[0])) v = (v * 100).toFixed(1) + '%';
            else if (ptsCols.includes(c[0])) v = (v > 0 ? '+' : '') + v.toFixed(1);
            else if (numCols.includes(c[0])) v = Number(v).toLocaleString('en-US');
            else v = esc(v);
            const href = links[c[0]] ? row[links[c[0]]] : null;
            if (href && v !== '') v = '<a href="' + href + '" target="_blank" title="Open in a new tab">' + v + '</a>';
            const title = isText(c[0]) && v ? ' title="' + v.replace(/"/g, '&quot;') + '"' : '';
            return '<td class="' + cellClass(c[0]) + '"' + title + '>' + v + '</td>';
          }).join('') + '</tr>';
        }
        if (!shown) html += '<tr><td colspan="' + res.cols.length + '" class="empty">no rows match</td></tr>';
        body.innerHTML = html + '</tbody></table>';
        body.querySelectorAll('th.drill-sort').forEach(th => th.addEventListener('click', () => {
          const k = th.dataset.k;
          if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = isNum(k) ? -1 : 1; }   // numbers biggest-first, text A→Z
          render();
        }));
      };
      render();
      window.__drillFilter = q => { filter = q.trim().toLowerCase(); render(); };
    }).catch(() => body.innerHTML = '<div class="empty">failed to load</div>');
  }
  function bind(u, opts) {
    url = u;
    asOf = (opts && opts.asOf) || null;
    document.addEventListener('click', e => {
      const el = e.target.closest('.drill');
      if (!el) return;
      e.preventDefault();
      // drills inside the assets/liabilities breakdown explain today's ledger, so they ignore the page's asOf cutoff
      open(el.closest('#liabmodal') ? Object.assign({noAsOf: true}, el.dataset) : el.dataset);
    });
    // Escape closes the top-most open modal only (drill above liabilities above the rest)
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      const open = [...document.querySelectorAll('.fin-modal')].filter(m => m.style.display === 'flex');
      if (!open.length) return;
      open.sort((a, b) => parseInt(b.style.zIndex || 60) - parseInt(a.style.zIndex || 60))[0].style.display = 'none';
    });
  }
  return {bind, open, fmt};
})();

window.PCALedger = (function () {
  const fmt = PCADrill.fmt;
  let urls = {}, charts = {};
  const money = v => (typeof PCA !== 'undefined' ? PCA.money(v) : v);   // PCA is a top-level const in base.html, not a window property

  function bind(map) {
    urls = map;   // {liabilities: url, assets: url}
    document.addEventListener('click', e => {
      const el = e.target.closest('[data-ledger-open]');
      if (!el) return;
      e.preventDefault();
      open(urls[el.dataset.ledgerOpen]);
    });
  }
  function open(url) {
    const modal = document.getElementById('liabmodal'), box = document.getElementById('liabbox');
    if (!url) return;
    box.innerHTML = '<div class="empty">loading the breakdown…</div>';
    modal.style.display = 'flex';
    fetch(url, {headers: {'X-Requested-With': 'fetch'}}).then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(html => { box.innerHTML = html; init(box.querySelector('#liab')); })
      .catch(() => box.innerHTML = '<div class="empty">failed to load</div>');
  }
  function destroyCharts() { Object.values(charts).forEach(c => c.destroy()); charts = {}; }
  function init(root) {
    if (!root) return;
    destroyCharts();
    const data = JSON.parse(root.querySelector('#liab-chart-data').textContent);
    const showTab = key => {
      root.querySelectorAll('.liab-tabs a').forEach(a => a.classList.toggle('active', a.dataset.liabTab === key));
      root.querySelectorAll('.liab-tab').forEach(t => { t.hidden = t.dataset.tab !== key; });
      if (key === 'trend') trendChart(root, data);
    };
    root.addEventListener('click', e => {
      const tab = e.target.closest('[data-liab-tab]');
      if (tab) { e.preventDefault(); showTab(tab.dataset.liabTab); return; }
      const jump = e.target.closest('[data-liab-jump]');
      if (jump) {
        e.preventDefault(); showTab('breakdown');
        const g = root.querySelector('#liab-g-' + jump.dataset.liabJump);
        setGroup(root, jump.dataset.liabJump, true);
        g.scrollIntoView({behavior: 'smooth', block: 'start'});
        g.classList.remove('liab-flash'); void g.offsetWidth; g.classList.add('liab-flash');
        return;
      }
      const more = e.target.closest('[data-liab-more]');
      if (more) {
        e.preventDefault();
        const row = more.closest('tr');
        const shown = row.dataset.shown === '1';
        row.dataset.shown = shown ? '0' : '1';
        root.querySelectorAll('tr.liab-cleared[data-group="' + more.dataset.liabMore + '"]').forEach(tr => { tr.hidden = shown; });
        more.textContent = (shown ? '+ show ' : '− hide ') + more.textContent.replace(/^[+−] (show|hide) /, '');
        return;
      }
      const close = e.target.closest('[data-liab-close]');
      if (close) { e.preventDefault(); document.getElementById('liabmodal').style.display = 'none'; return; }
      if (e.target.closest('a, button, input, canvas, .drill, tr.liab-detail')) return;
      const grp = e.target.closest('tr.liab-group');
      if (grp) { setGroup(root, grp.dataset.group, null); return; }
      const acct = e.target.closest('tr.liab-acct');
      if (acct) { toggleAcct(root, acct.dataset.acct, data); }
    });
  }
  function setGroup(root, key, force) {
    const g = root.querySelector('#liab-g-' + key);
    const open = force == null ? g.dataset.open !== '1' : force;
    g.dataset.open = open ? '1' : '0';
    g.querySelector('.caret').textContent = open ? '▾' : '▸';
    const more = root.querySelector('tr.liab-more[data-group="' + key + '"]');
    const clearedShown = more && more.dataset.shown === '1';
    root.querySelectorAll('tr.liab-acct[data-group="' + key + '"]').forEach(tr => {
      tr.hidden = !open || (tr.classList.contains('liab-cleared') && !clearedShown);
    });
    if (more) more.hidden = !open;
    if (!open) root.querySelectorAll('tr.liab-detail[data-group="' + key + '"]').forEach(tr => { tr.hidden = true; });
  }
  function toggleAcct(root, acct, data) {
    const tr = root.querySelector('tr.liab-detail[data-for="' + acct + '"]');
    const row = root.querySelector('tr.liab-acct[data-acct="' + acct + '"]');
    const open = tr.hidden;
    tr.hidden = !open;
    row.querySelector('.caret').textContent = open ? '▾' : '▸';
    if (open && !charts[acct]) {
      const c = tr.querySelector('canvas[data-liab-chart]');
      if (c) charts[acct] = lineChart(c, data.labels, data.accounts[acct]);
    }
  }
  const axes = { y: { ticks: { callback: v => money(v) }, grid: { color: '#eef2f7' } }, x: { ticks: { maxTicksLimit: 7 }, grid: { display: false } } };
  function lineChart(canvas, labels, series) {
    return new Chart(canvas, {
      data: { labels, datasets: [{ type: 'line', label: 'Month-end balance', data: series, borderColor: '#1f5eff',
        backgroundColor: 'rgba(31,94,255,.10)', fill: true, pointRadius: 2, tension: .2, spanGaps: true }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmt(c.raw) } } }, scales: axes } });
  }
  function trendChart(root, data) {
    if (charts.__trend) return;
    const canvas = root.querySelector('#liab-trend');
    if (!canvas) return;
    const totals = data.labels.map((_, i) => data.groups.reduce((s, g) => s + (g.data[i] || 0), 0));
    charts.__trend = new Chart(canvas, {
      data: { labels: data.labels, datasets: [
        ...data.groups.map(g => ({ type: 'bar', label: g.label, data: g.data, backgroundColor: g.color + 'cc', stack: 'liab', borderRadius: 1 })),
        { type: 'line', label: 'Total', data: totals, borderColor: '#14202e', borderWidth: 2, pointRadius: 2, tension: .15, order: -1 } ] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: c => c.dataset.label + ': ' + fmt(c.raw) } } },
        scales: { y: { stacked: true, ticks: { callback: v => money(v) }, grid: { color: '#eef2f7' } }, x: { stacked: true, ticks: { maxTicksLimit: 10 }, grid: { display: false } } } } });
  }
  return {bind, open, init};
})();
