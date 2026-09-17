/* Catalog search page: server-side scored search (paged, no cap), selected-product card, compare (≤ 6), and the
   Estimate Summary rail (working estimate, quick add, running totals). PCA.pref / PCA.rememberFilters come from base.html. */
(function () {
  const CFG = JSON.parse(document.getElementById('est-cfg').textContent);
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const money = v => v == null ? '<span class="muted">N/A</span>' : '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const csrf = () => { const m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ''; };
  const toastEl = $('toast');
  let toastT;
  function toast(msg) { toastEl.textContent = msg; toastEl.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('on'), 2200); }

  const S = {q: CFG.q || '', brand: CFG.brand || '', hasCost: PCA.pref('hasCost', true), sort: PCA.pref('sort', 'match'), page: 1,
             per: PCA.pref('per', 100), rows: [], total: 0, pages: 0, sel: null, compare: PCA.pref('compare', []) || [], tab: PCA.pref('tab', 'results'),
             est: PCA.pref('est', CFG.est_id || '') || '', room: PCA.pref('room', '') || '', estData: null, archived: false};
  if (CFG.est_id) { S.est = String(CFG.est_id); PCA.setPref('est', S.est); }
  let searchT, quickT, quickRows = [], quickIdx = -1;

  function url() {
    const p = new URLSearchParams({q: S.q, brand: S.brand, has_cost: S.hasCost ? '1' : '0', sort: S.sort, page: S.page, per: S.per});
    if (S.archived) p.set('archived', '1');
    return CFG.search_url + '?' + p.toString();
  }
  function remember() {
    const p = new URLSearchParams(location.search);
    if (S.q) p.set('q', S.q); else p.delete('q');
    if (S.brand) p.set('brand', S.brand); else p.delete('brand');
    history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : ''));
    PCA.rememberFilters();
    PCA.setPref('hasCost', S.hasCost); PCA.setPref('sort', S.sort); PCA.setPref('per', S.per);
  }
  function run(keepPage) {
    if (!keepPage) S.page = 1;
    remember();
    if (!S.q && !S.brand) { S.rows = []; S.total = 0; render(); return; }
    $('results').innerHTML = '<div class="empty">searching…</div>';
    fetch(url()).then(r => r.json()).then(res => {
      S.rows = res.rows; S.total = res.total; S.pages = res.pages; S.page = res.page; S.capped = res.capped; S.ms = res.ms;
      if (!S.sel || !S.rows.some(r => r.id === S.sel.id)) S.sel = S.rows[0] || null;
      render();
    }).catch(() => { $('results').innerHTML = '<div class="empty">search failed — try again</div>'; });
  }
  function inCompare(id) { return S.compare.includes(id); }
  function inEstimate(r) { return S.estData && S.estData.parts && S.estData.parts.has(r.norm); }

  function render() {
    // sort chips + column state
    document.querySelectorAll('[data-sort]').forEach(c => c.classList.toggle('primary', c.dataset.sort === S.sort));
    $('hascost').checked = !!S.hasCost;
    $('archived').checked = !!S.archived;
    $('nb-cmp').textContent = S.compare.length;
    $('tab-results').classList.toggle('primary', S.tab === 'results'); $('tab-compare').classList.toggle('primary', S.tab === 'compare');
    $('v-results').hidden = S.tab !== 'results'; $('v-compare').hidden = S.tab !== 'compare';
    renderSel(); renderTable(); renderPager();
    if (S.tab === 'compare') renderCompare();
  }
  function renderSel() {
    const box = $('selcard'), it = S.sel;
    if (!it) { box.hidden = true; return; }
    box.hidden = false;
    const mar = it.margin == null ? '—' : it.margin.toFixed(1) + '%';
    const cls = it.conf >= 75 ? 'ch' : it.conf >= 50 ? 'cm' : 'cl';
    box.innerHTML = '<div class="est-sel"><div><div style="font-size:12px;color:var(--muted)">' + esc(it.mfr) + (it.mfr_raw && it.mfr_raw !== it.mfr ? ' <span class="muted" title="As the vendor file spelled it">(' + esc(it.mfr_raw) + ')</span>' : '') + '</div>' +
      '<div style="font-size:19px;font-weight:700"><a href="' + CFG.item_url.replace('/0/', '/' + it.id + '/') + '" title="Price history across every vendor file that lists this part">' + esc(it.part) + '</a></div>' +
      '<div>' + esc(it.desc) + '</div><div class="note" style="margin-top:4px">' + esc(it.source) + (it.category ? ' · ' + esc(it.category) : '') + (it.date ? ' · Source date ' + esc(it.date) : ' · undated') + (it.archived ? ' · <span class="pill bad">archived: ' + esc(it.reason) + '</span>' : '') + '</div>' +
      '<div class="tiles"><div class="tile" title="Dealer cost in the vendor price list"><div class="l">Dealer cost</div><div class="v cost">' + money(it.cost) + '</div></div>' +
      '<div class="tile" title="Manufacturer list / MSRP"><div class="l">MSRP / List</div><div class="v msrp">' + money(it.msrp) + '</div></div>' +
      '<div class="tile" title="Minimum advertised price"><div class="l">MAP</div><div class="v">' + money(it.map) + '</div></div>' +
      '<div class="tile" title="Date carried by the vendor file name or its Effective Date column"><div class="l">Source date</div><div class="v" style="font-size:13px">' + esc(it.date || '—') + '</div></div>' +
      '<div class="tile" title="(MSRP − cost) ÷ MSRP"><div class="l">Margin cost→MSRP</div><div class="v mar">' + mar + '</div></div></div></div>' +
      '<div style="text-align:right"><span class="ctag ' + cls + '" title="+30 cost · +15 MSRP · +15 description · +25 source year ≥ 2025 (else +15 for a 2020s year) · +15 MAP">' + esc(it.conf_label) + '</span><div class="note">' + it.conf + ' / 100</div>' +
      (S.q ? '<div style="margin-top:6px"><span class="match ' + it.score_class + '">' + esc(it.score_label) + '</span></div>' : '') +
      '<div style="margin-top:10px;display:flex;gap:6px;justify-content:flex-end">' + (CFG.can_write ? '<button class="btn small primary" data-add="' + it.id + '">+ Add</button>' : '') +
      '<button class="btn small" data-cmp="' + it.id + '">' + (inCompare(it.id) ? '✓ Comparing' : 'Compare') + '</button></div></div></div>';
  }
  function th(key, label, title, cls) {
    const on = S.sort.startsWith(key + ':'), dir = on ? S.sort.split(':')[1] : '';
    return '<th class="sortable ' + (cls || '') + (on ? ' on' : '') + '" data-col="' + key + '" title="' + esc(title) + '">' + label + (on ? (dir === 'desc' ? ' ↓' : ' ↑') : '') + '</th>';
  }
  function renderTable() {
    const box = $('results');
    if (!S.q && !S.brand) { box.innerHTML = '<div class="empty">Type a part number, a brand or words from a description — or pick a brand to browse it.</div>'; return; }
    if (!S.rows.length) { box.innerHTML = '<div class="empty">No matches' + (S.hasCost ? ' — try turning off <b>Has cost</b>' : '') + '.</div>'; return; }
    let h = '<div class="table-wrap freeze tall"><table class="data dense" id="restable"><thead><tr>' + th('mfr', 'Brand', 'Cleaned manufacturer (alias table; part-number lookalikes grouped as Other)') +
      th('part', 'Part #', 'Vendor part / model number') + '<th>Description</th>' + th('cost', 'Cost', 'Dealer cost', 'num') + th('msrp', 'MSRP', 'List price', 'num') +
      '<th class="num" title="Minimum advertised price">MAP</th>' + th('date', 'Dated', 'Date the vendor file carries (its name or Effective Date column)') + th('margin', 'Margin', '(MSRP − cost) ÷ MSRP', 'num') +
      (S.q ? th('score', 'Match', 'Exact 100 · exact part 99 · prefix 85 · contains 70 · brand 55 · description 45 · word overlap 25–45') : '') +
      '<th title="Confidence 0–100 from cost / MSRP / description / source year / MAP">Conf</th><th></th></tr></thead><tbody>';
    for (const r of S.rows) {
      h += '<tr class="' + (S.sel && S.sel.id === r.id ? 'sel' : '') + '" data-id="' + r.id + '"><td>' + esc(r.mfr) + '</td><td class="mono"><a href="' + CFG.item_url.replace('/0/', '/' + r.id + '/') + '" title="Open this part: price history across files">' + esc(r.part) + '</a></td>' +
        '<td class="trunc fluid" title="' + esc(r.desc) + '">' + esc(r.desc) + '</td><td class="num">' + money(r.cost) + '</td><td class="num">' + money(r.msrp) + '</td><td class="num">' + (r.map == null ? '' : money(r.map)) + '</td>' +
        '<td>' + esc(r.date || '') + (r.archived ? ' <span class="tag" title="Hidden by hygiene: ' + esc(r.reason) + '">' + esc(r.reason) + '</span>' : '') + '</td><td class="num">' + (r.margin == null ? '' : r.margin.toFixed(1) + '%') + '</td>' +
        (S.q ? '<td><span class="match ' + r.score_class + '">' + esc(r.score_label) + '</span></td>' : '') + '<td>' + r.conf + '</td>' +
        '<td style="white-space:nowrap">' + (CFG.can_write ? '<button class="btn small" data-add="' + r.id + '" title="Add one to the working estimate">' + (inEstimate(r) ? '✓ In' : '+ Add') + '</button> ' : '') +
        '<button class="btn small" data-cmp="' + r.id + '" title="Compare up to 6">' + (inCompare(r.id) ? '✓' : 'Cmp') + '</button></td></tr>';
    }
    box.innerHTML = h + '</tbody></table></div>';
  }
  function renderPager() {
    const p = $('pager');
    if (!S.total) { p.innerHTML = ''; return; }
    const a = (S.page - 1) * S.per + 1, b = Math.min(S.total, S.page * S.per);
    p.innerHTML = '<span>' + a.toLocaleString() + '–' + b.toLocaleString() + ' of <b>' + S.total.toLocaleString() + '</b> matches' + (S.capped ? ' <span class="warn-ink" title="The query matched more than the candidate cap; narrow it (brand, more of the part number)">(first ' + S.total.toLocaleString() + ' candidates scored)</span>' : '') + ' · ' + S.ms + ' ms</span>' +
      '<span><button class="btn small" id="pg-prev" ' + (S.page <= 1 ? 'disabled' : '') + '>‹ Prev</button> page ' + S.page + ' / ' + S.pages + ' <button class="btn small" id="pg-next" ' + (S.page >= S.pages ? 'disabled' : '') + '>Next ›</button> · per page ' +
      '<select id="pg-per">' + [50, 100, 250, 500].map(n => '<option ' + (n == S.per ? 'selected' : '') + '>' + n + '</option>').join('') + '</select></span>';
    $('pg-prev').onclick = () => { S.page--; run(true); }; $('pg-next').onclick = () => { S.page++; run(true); };
    $('pg-per').onchange = e => { S.per = +e.target.value; run(); };
  }
  function renderCompare() {
    const box = $('cmp-grid');
    if (!S.compare.length) { box.innerHTML = '<div class="empty">No items in compare yet — click <b>Cmp</b> on a result (up to 6).</div>'; return; }
    fetch(CFG.search_url + '?ids=' + S.compare.join(',')).then(r => r.json()).then(res => {
      const rows = res.rows.slice().sort((x, y) => S.compare.indexOf(x.id) - S.compare.indexOf(y.id));
      const costs = rows.map(r => r.cost).filter(c => c), margs = rows.map(r => r.margin).filter(m => m != null);
      const minC = costs.length ? Math.min(...costs) : null, maxM = margs.length ? Math.max(...margs) : null;
      box.innerHTML = rows.map(r => {
        const bc = r.cost && r.cost === minC, bm = r.margin != null && maxM != null && Math.abs(r.margin - maxM) < 0.01, win = bc || bm;
        return '<div class="cmpcard' + (win ? ' win' : '') + '"><button class="rm" data-uncmp="' + r.id + '" title="Remove from compare">×</button>' + (win ? '<div class="best">★ Best value</div>' : '') +
          '<div style="font-size:11.5px;color:var(--muted)">' + esc(r.mfr) + '</div><b><a href="' + CFG.item_url.replace('/0/', '/' + r.id + '/') + '">' + esc(r.part) + '</a></b><div class="note">' + esc(r.desc) + '</div>' +
          '<dl class="kv dense"><dt>Dealer cost</dt><dd class="' + (bc ? 'b' : '') + '">' + money(r.cost) + (bc ? ' <span class="pill good">best</span>' : '') + '</dd><dt>MSRP</dt><dd>' + money(r.msrp) + '</dd><dt>MAP</dt><dd>' + (r.map == null ? '—' : money(r.map)) + '</dd>' +
          '<dt>Margin</dt><dd class="' + (bm ? 'b' : '') + '">' + (r.margin == null ? '—' : r.margin.toFixed(1) + '%') + (bm ? ' <span class="pill good">best</span>' : '') + '</dd><dt>Source date</dt><dd>' + esc(r.date || '—') + '</dd><dt>Source</dt><dd class="note">' + esc(r.source) + '</dd></dl>' +
          (CFG.can_write ? '<div style="margin-top:8px"><button class="btn small primary" data-add="' + r.id + '">+ Add to estimate</button></div>' : '') + '</div>';
      }).join('');
    });
  }

  // ---- rail: working estimate
  function loadEst() {
    const box = $('rail-body');
    if (!S.est) { box.innerHTML = '<div class="empty" style="padding:14px">Pick an estimate (or start one) to add items to.</div>'; S.estData = null; renderTable(); return; }
    fetch(CFG.detail_url.replace('/0/', '/' + S.est + '/') + '?format=json').then(r => r.ok ? r.json() : null).then(d => {
      if (!d) { S.est = ''; PCA.setPref('est', ''); loadEst(); return; }
      d.parts = new Set(); d.rooms.forEach(r => r.lines.forEach(l => d.parts.add(l.part_norm)));
      S.estData = d;
      if (!d.rooms.some(r => String(r.id) === String(S.room))) S.room = d.rooms.length ? String(d.rooms[0].id) : '';
      const g = d.grand, lines = [];
      d.rooms.forEach(r => r.lines.forEach(l => lines.push({room: r.name, ...l})));
      const roomSel = '<select id="rail-room" title="Room the quick add goes into">' + d.rooms.map(r => '<option value="' + r.id + '" ' + (String(r.id) === String(S.room) ? 'selected' : '') + '>' + esc(r.name) + ' (' + r.lines.length + ')</option>').join('') + '</select>';
      box.innerHTML = '<div class="row" style="justify-content:space-between;margin-bottom:6px"><b>' + lines.length + ' item' + (lines.length === 1 ? '' : 's') + ' · ' + d.rooms.length + ' room' + (d.rooms.length === 1 ? '' : 's') + '</b>' + roomSel + '</div>' +
        (CFG.can_write ? '<div class="quick"><input id="quick" placeholder="type part # and Enter to add the top hit" autocomplete="off"><div class="sugg" id="quick-sugg" hidden></div></div>' : '') +
        '<div class="rail-tot"><div class="t" title="Equipment cost × qty + labor hours × cost rate"><div class="l">Total cost</div><div class="v">' + money(g.total_cost) + '</div></div><div class="t" title="Sell × qty + labor hours × sell rate"><div class="l">Total sell</div><div class="v pos">' + money(g.total_sell) + '</div></div>' +
        '<div class="t" title="Sell − cost"><div class="l">Profit</div><div class="v">' + money(g.profit) + '</div></div><div class="t" title="Profit ÷ sell"><div class="l">Margin</div><div class="v">' + (g.margin == null ? '—' : g.margin.toFixed(1) + '%') + '</div></div></div>' +
        '<div class="rail-lines">' + (lines.length ? lines.slice(-12).reverse().map(l => '<div class="ln"><b>' + esc(l.manufacturer) + ' ' + esc(l.part) + '</b><span class="muted">' + esc(l.room) + ' · ×' + l.qty + ' @ ' + money(l.cost) + ' → ' + money(l.sell) + '</span></div>').join('') : '<div class="empty" style="padding:12px">nothing added yet</div>') + '</div>' +
        '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap"><a class="btn small primary" href="' + CFG.detail_url.replace('/0/', '/' + S.est + '/') + '">Full estimate builder</a><a class="btn small" href="' + CFG.export_url.replace('/0/', '/' + S.est + '/') + '?fmt=xlsx">Export xlsx</a></div>';
      $('rail-room') && ($('rail-room').onchange = e => { S.room = e.target.value; PCA.setPref('room', S.room); });
      bindQuick(); renderTable();
    });
  }
  function bindQuick() {
    const q = $('quick'), sg = $('quick-sugg');
    if (!q) return;
    q.addEventListener('input', () => { clearTimeout(quickT); quickT = setTimeout(() => {
      const v = q.value.trim(); if (!v) { sg.hidden = true; return; }
      fetch(CFG.search_url + '?' + new URLSearchParams({q: v, per: 8, has_cost: '0'})).then(r => r.json()).then(res => {
        quickRows = res.rows; quickIdx = quickRows.length ? 0 : -1;
        sg.innerHTML = quickRows.map((r, i) => '<div data-i="' + i + '" class="' + (i === quickIdx ? 'on' : '') + '"><b>' + esc(r.part) + '</b> · ' + esc(r.mfr) + ' · ' + money(r.cost) + ' <span class="match ' + r.score_class + '">' + esc(r.score_label) + '</span></div>').join('') || '<div class="muted">no match</div>';
        sg.hidden = false;
      }); }, 160); });
    q.addEventListener('keydown', e => {
      if (e.key === 'Escape') { q.value = ''; sg.hidden = true; }
      else if (e.key === 'ArrowDown') { quickIdx = Math.min(quickRows.length - 1, quickIdx + 1); paint(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { quickIdx = Math.max(0, quickIdx - 1); paint(); e.preventDefault(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (quickIdx >= 0 && quickRows[quickIdx]) { add(quickRows[quickIdx].id); q.value = ''; sg.hidden = true; } }
    });
    sg.addEventListener('click', e => { const d = e.target.closest('[data-i]'); if (d) { add(quickRows[+d.dataset.i].id); q.value = ''; sg.hidden = true; } });
    function paint() { sg.querySelectorAll('[data-i]').forEach(d => d.classList.toggle('on', +d.dataset.i === quickIdx)); }
  }
  function add(itemId) {
    if (!S.est) { toast('Pick a working estimate first'); $('rail-est').focus(); return; }
    fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({action: 'add_line', id: S.est, item_id: itemId, room_id: S.room})})
      .then(r => r.json()).then(res => {
        if (!res.ok) { toast(res.error || 'could not add'); return; }
        const r = S.rows.find(x => x.id === itemId) || (S.sel && S.sel.id === itemId ? S.sel : null);
        toast('Added line: ' + (r ? r.part : itemId));
        loadEst();
      });
  }

  // ---- events
  document.addEventListener('click', e => {
    const add_ = e.target.closest('[data-add]'); if (add_) { add(+add_.dataset.add); return; }
    const cmp = e.target.closest('[data-cmp]');
    if (cmp) {
      const id = +cmp.dataset.cmp;
      if (inCompare(id)) S.compare = S.compare.filter(x => x !== id);
      else if (S.compare.length >= CFG.compare_limit) { toast('Compare holds ' + CFG.compare_limit + ' items — remove one first'); return; }
      else S.compare.push(id);
      PCA.setPref('compare', S.compare); render(); return;
    }
    const un = e.target.closest('[data-uncmp]'); if (un) { S.compare = S.compare.filter(x => x !== +un.dataset.uncmp); PCA.setPref('compare', S.compare); render(); return; }
    const srt = e.target.closest('[data-sort]'); if (srt) { S.sort = srt.dataset.sort; run(); return; }
    const col = e.target.closest('th[data-col]'); if (col) { const k = col.dataset.col; S.sort = S.sort === k + ':asc' ? k + ':desc' : k + ':asc'; run(); return; }
    const tr = e.target.closest('#restable tbody tr'); if (tr && !e.target.closest('a,button')) { S.sel = S.rows.find(r => r.id === +tr.dataset.id); render(); return; }
    const ex = e.target.closest('[data-example]'); if (ex) { $('si').value = ex.dataset.example; S.q = ex.dataset.example; run(); return; }
    const tab = e.target.closest('[data-tab]'); if (tab) { S.tab = tab.dataset.tab; PCA.setPref('tab', S.tab); render(); return; }
    if (e.target.id === 'cmp-clear') { S.compare = []; PCA.setPref('compare', []); render(); }
    if (e.target.id === 'brand-clear') { S.brand = ''; $('brand').value = ''; run(); }
  });
  $('si').addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(() => { S.q = $('si').value.trim(); run(); }, 160); });
  $('si').addEventListener('keydown', e => { if (e.key === 'Escape') { $('si').value = ''; S.q = ''; run(); } });
  $('brand').addEventListener('change', () => { S.brand = $('brand').value.trim(); run(); });
  $('brand').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); const v = $('brand').value.trim().toLowerCase(); const hit = CFG.brands.find(b => b.toLowerCase() === v) || CFG.brands.find(b => b.toLowerCase().includes(v)); if (hit) { $('brand').value = hit; S.brand = hit; run(); } } if (e.key === 'Escape') { $('brand').value = ''; S.brand = ''; run(); } });
  $('hascost').addEventListener('change', e => { S.hasCost = e.target.checked; run(); });
  $('archived').addEventListener('change', e => { S.archived = e.target.checked; run(); });
  $('rail-est').addEventListener('change', e => { S.est = e.target.value; PCA.setPref('est', S.est); S.room = ''; loadEst(); });
  const newf = $('rail-new');
  if (newf) newf.addEventListener('submit', e => {
    e.preventDefault();
    const title = $('rail-new-title').value.trim();
    fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({action: 'create', title})})
      .then(r => r.json()).then(res => { if (res.ok) { const o = document.createElement('option'); o.value = res.id; o.textContent = title || ('Estimate ' + res.id); o.selected = true; $('rail-est').prepend(o); S.est = String(res.id); PCA.setPref('est', S.est); $('rail-new-title').value = ''; loadEst(); toast('Estimate started'); } });
  });

  // ---- boot
  $('si').value = S.q; $('brand').value = S.brand;
  if (S.est) $('rail-est').value = S.est;
  if (!$('rail-est').value) { S.est = ''; }
  loadEst();
  run();
})();
