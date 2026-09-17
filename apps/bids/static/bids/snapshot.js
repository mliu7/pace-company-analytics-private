// Pipeline Snapshot: the window table (submitted / awarded / lost / due as sections of ONE table so every column lines
// up, each with a subtotal), the open-proposal and quoting tables — every row loaded (no cap), compact select filters,
// multi-key sort (shift-click), zebra rows, a totals row, and an expandable detail row (win-rate breakdown, documents,
// portal facts). Prefs remembered per table under PCA.pref. Chart.js + PCA come from base.html.
window.PCASnapshot = (function () {
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const money0 = v => v == null ? '—' : v.toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0});
  const pct = (v, d) => v == null ? '—' : (v * 100).toFixed(d == null ? 0 : d) + '%';
  const fmtDate = s => s ? new Date(s.slice(0, 10) + 'T00:00:00').toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: '2-digit'}) : '—';
  const STAGE = {quoting: 'warn', submitted: 'info', awarded: 'good', lost: 'bad', on_hold: ''};
  const BAD = new Set(['Missing bidder', 'Submitted, no value', 'Past due', 'No budget', 'Stale 100d+']);
  const link = (text, url, cls) => url ? '<a href="' + url + '"' + (cls ? ' class="' + cls + '"' : '') + '>' + esc(text) + '</a>' : esc(text);

  // ---- columns: [label, render, sortKind, sortValue, cellClass, totals?]
  const COLS = {
    name: ['Proposal', r => link(r.name, r.url) + (r.job ? ' <a class="muted" href="' + r.job_url + '" title="SL job" style="font-weight:400">' + esc(r.job) + '</a>' : ''), 'text', r => r.name, 'name'],
    client: ['Client', r => link(r.client, r.client_url), 'text', r => r.client, 'client'],
    att: ['Flags', r => r.attention.length ? r.attention.map(a => '<span class="att' + (BAD.has(a) ? ' bad' : '') + '">' + esc(a) + '</span>').join('') : '<span class="muted">—</span>', 'num', r => r.attention.length, 'attcell'],
    div: ['Div', r => esc(r.div), 'text', r => r.div, 'tag-cell'],
    est: ['Estimator', r => link(r.est, r.est_url) + (r.inferred ? ' <span class="tag" title="Bidder was blank; credited by rule">inf</span>' : ''), 'text', r => r.est, 'est'],
    type: ['Type', r => esc(r.type), 'text', r => r.type, 'tag-cell'],
    stage: ['Stage', r => '<span class="pill ' + (STAGE[r.stage] || '') + '">' + esc(r.stage_label) + '</span>', 'text', r => r.stage_label],
    submitted: ['Submitted', r => fmtDate(r.submitted), 'date', r => r.submitted, 'dt'],
    due: ['Bid due', r => fmtDate(r.due), 'date', r => r.due, 'dt'],
    awarded: ['Awarded', r => fmtDate(r.awarded), 'date', r => r.awarded, 'dt'],
    age: ['Days waiting', r => r.age == null ? '—' : r.age, 'num', r => r.age, 'dim'],
    value: ['Value', r => money0(r.value), 'num', r => r.value, 'money strong', 'value'],
    budget: ['Budget', r => money0(r.budget), 'num', r => r.budget, 'dim', 'budget'],
    gp: ['GP quoted', r => money0(r.gp), 'num', r => r.gp, 'money', 'gp'],
    gp_pct: ['GP %', r => pct(r.gp_pct, 1), 'num', r => r.gp_pct, 'dim', 'gp_pct'],
    prob: ['Stated', r => r.prob == null ? '<span class="muted">unscored</span>' : '<span class="pbar" title="the estimator\'s Probability of Close"><i style="width:' + r.prob + '%"></i></span>' + r.prob + '%', 'num', r => r.prob],
    pca: ['PCA rate', r => '<span class="pbar pca" title="PCA\'s estimated win rate — expand the row for the factors"><i style="width:' + Math.round(r.pca * 100) + '%"></i></span>' + pct(r.pca), 'num', r => r.pca],
    exp_gp: ['Exp GP · stated', r => money0(r.exp_gp), 'num', r => r.exp_gp, 'money', 'exp_gp'],
    exp_gp_pca: ['Exp GP · PCA', r => money0(r.exp_gp_pca), 'num', r => r.exp_gp_pca, 'money', 'exp_gp_pca'],
    act: ['Last activity', r => r.act ? '<span title="' + esc(r.act.text) + '">' + fmtDate(r.act.date) + '</span> <span class="muted" style="font-weight:400">' + esc(r.act.text.length > 34 ? r.act.text.slice(0, 33) + '…' : r.act.text) + '</span>' : '<span class="muted">—</span>', 'date', r => r.act ? r.act.date : '', 'actcell'],
    docs: ['Docs', r => r.docs.length ? '<span class="tag" title="files linked to this bid — expand the row">' + r.docs.length + '</span>' : (r.folder ? '<span class="muted" title="job folder exists, no files yet">folder</span>' : '<span class="muted">—</span>'), 'num', r => r.docs.length, 'dim'],
    ball: ['Ball in court', r => esc(r.ball), 'text', r => r.ball, 'tag-cell'],
  };
  // the window's four sections share ONE column set so they line up; open / quoting add the columns that matter there
  // header hovers — every column explains itself (Owner, 2026-09-11)
  const TIPS = {
    name: 'The proposal as named in the Project Portal; the SL job number follows once the bid is awarded and linked. Click a row anywhere else to expand its details.',
    client: 'The client as typed in the portal, resolved to the SL customer when one matches — click to open the customer page.',
    att: 'Portal hygiene flags: missing bidder, missing due date, no value, no budget, no submitted date, BOM needed, past due (quotes only), unscored, stale.',
    value: 'Project Value as typed in the portal — the price quoted to the client.',
    budget: 'Budget as typed in the portal — the estimated cost.',
    gp: 'GP quoted = value − budget: the gross profit the estimate carries.',
    gp_pct: 'GP quoted ÷ value.',
    prob: 'Stated chance: the estimator\'s Probability of Close from the portal (blank = unscored, never 0 %).',
    pca: 'PCA\'s estimated win rate: the historical hit rate of this estimator, client, division, work type, size band, sector and rep on decided bids since 2019, blended and weighted by evidence. Expand the row for the factors.',
    exp_gp: 'Expected GP at the stated chance = GP quoted × Probability of Close.',
    exp_gp_pca: 'Expected GP at PCA\'s rate = GP quoted × PCA\'s estimated win rate.',
    submitted: 'The portal\'s Date Submitted.',
    due: 'The portal\'s Bid Due Date.',
    awarded: 'The portal\'s Date Awarded.',
    age: 'Days waiting: days since the proposal was submitted, i.e. how long the client has had it without a decision.',
    act: 'The most recent thing that happened to this bid: a portal edit (what changed, by whom), a file added or updated in its folder, a PCA note or follow-up, or its creation.',
    stage: 'Portal status, normalised (quoting, submitted, awarded, lost, on hold).',
    div: 'Division: the SL job\'s when linked, else the estimator\'s.',
    est: 'Estimator of record (the portal\'s Bidder) — click to open the estimator page. "inf" = the Bidder was blank and PCA inferred the person.',
    type: 'Work type read from the project name (cameras, access control, AV, network, IT services, box sale, service, RFP); the portal has no type column.',
    docs: 'Files linked to this bid on the P: drive / SharePoint; "folder" = the job folder exists but holds no files yet. Expand the row to open them.',
    ball: 'Ball in court, as set in the portal.',
  };
  // identity, then the money and the odds (what Owner scans first), then the who / what / when, then docs; the window
  // table keeps PCA's two columns at the far right (Owner, 2026-09-11)
  const WINDOW_COLS = ['name', 'client', 'att', 'value', 'budget', 'gp', 'gp_pct', 'prob', 'exp_gp', 'stage', 'submitted', 'div', 'est', 'type', 'docs', 'pca', 'exp_gp_pca'];
  // open and quoting share one layout — quoting swaps Submitted / Days waiting for Stage / Bid due (Owner, 2026-09-11)
  const SETS = {
    window: WINDOW_COLS,
    open: ['name', 'client', 'att', 'value', 'budget', 'gp', 'gp_pct', 'prob', 'exp_gp', 'submitted', 'age', 'act', 'div', 'est', 'type', 'docs', 'pca', 'exp_gp_pca'],
    quoting: ['name', 'client', 'att', 'value', 'budget', 'gp', 'gp_pct', 'prob', 'exp_gp', 'stage', 'due', 'act', 'div', 'est', 'type', 'docs', 'pca', 'exp_gp_pca'],
  };
  const TITLES = {window: 'This window', open: 'Open proposals', quoting: 'Quotes in progress'};
  const DEFAULT_SORT = {window: [['value', -1]], open: [['submitted', -1]], quoting: [['act', -1]]};
  const ACT_BUCKET = r => r.act_days == null ? ['no activity'] : r.act_days <= 7 ? ['last 7 days'] : r.act_days <= 30 ? ['last 30 days'] : r.act_days <= 90 ? ['last 90 days'] : ['older than 90 days'];
  const ACT_ORDER = ['last 7 days', 'last 30 days', 'last 90 days', 'older than 90 days', 'no activity'];
  // compact select filters: [key, label, values(row) -> [..]]
  const FILTERS = [['act', 'Activity', ACT_BUCKET], ['att', 'Attention', r => r.attention.length ? r.attention : ['(clean)']], ['div', 'Division', r => [r.div || '(none)']], ['est', 'Estimator', r => [r.est || '(no bidder)']],
                   ['type', 'Type', r => [r.type]], ['size', 'Size', r => [r.size]], ['prob', 'Stated', r => [r.prob == null ? 'unscored' : r.prob + '%']]];
  const SECTIONS = [['window', 'Submitted in the window'], ['awarded', 'Awarded in the window'], ['lost', 'Lost in the window'], ['due', 'Due in the window']];

  function detail(r) {
    const bd = r.breakdown.map(b => '<tr><td>' + esc(b.factor.replace('_', ' ')) + '</td><td class="trunc" style="max-width:160px" title="' + esc(b.level) + '">' + esc(b.level) + '</td><td class="num">' + (b.rate == null ? '—' : pct(b.rate)) + '</td><td class="num muted">' + (b.n || '—') + '</td><td class="num ' + (b.shift > 0 ? 'pos' : b.shift < 0 ? 'neg' : 'muted') + '">' + (b.n ? (b.shift > 0 ? '+' : '') + b.shift.toFixed(2) : '—') + '</td></tr>').join('');
    const docs = r.docs.length ? r.docs.map(d => '<div><a href="' + d.url + '" title="' + esc(d.sub) + '">' + esc(d.name) + '</a>' + (d.open ? '<a class="ext" href="' + d.open + '" target="_blank" rel="noopener" title="open at the source · ' + esc(d.unc) + '">↗</a>' : '') + ' <span class="muted">' + esc(d.sub) + ' · ' + fmtDate(d.mtime) + '</span></div>').join('') : '<div class="muted">No files linked yet.</div>';
    const folder = r.folder ? '<div style="margin-top:4px">Job folder: <a href="' + r.folder.open + '" target="_blank" rel="noopener" title="' + esc(r.folder.unc) + '">' + esc(r.folder.path) + ' ↗</a></div>' : '';
    const facts = [['Portal ID', r.pid], ['Stage', r.stage_label + (r.flag ? ' · ' + r.flag : '')], ['Submitted', fmtDate(r.submitted)], ['Bid due', fmtDate(r.due)], ['Awarded', fmtDate(r.awarded)], ['Sector', r.sector || '—'], ['Size band', r.size], ['Rep', r.rep || '—'], ['Ball in court', r.ball || '—'], ['BOM', r.bom || '—'], ['Walkthrough', r.walk || '—'], ['Last portal edit', fmtDate(r.modified)], ['Expected value', money0(r.exp_value)], ['Notes / follow-ups', r.notes + ' / ' + r.followups]]
      .map(([k, v]) => '<tr><td class="muted">' + k + '</td><td>' + esc(v) + '</td></tr>').join('');
    return '<div class="detail"><div><h4>Why ' + pct(r.pca) + ' — the factors behind PCA\'s win rate</h4><table class="data dense bd"><thead><tr><th>Factor</th><th>Level</th><th class="num">Hit rate</th><th class="num">Decided</th><th class="num" title="shift in log-odds after weighting and damping">Shift</th></tr></thead><tbody>' + bd + '</tbody></table>' +
      '<div class="muted" style="margin-top:4px">Stated by the estimator: ' + (r.prob == null ? 'unscored' : r.prob + '%') + ' · GP quoted ' + money0(r.gp) + ' → expected GP ' + money0(r.exp_gp) + ' stated / ' + money0(r.exp_gp_pca) + ' PCA</div></div>' +
      '<div class="docs"><h4>Documents</h4>' + docs + folder + '</div>' +
      '<div><h4>Portal facts</h4><table class="data dense bd"><tbody>' + facts + '</tbody></table><div style="margin-top:6px"><a class="btn small" href="' + r.url + '">Bid page</a> ' + (r.portal ? '<a class="btn small" href="' + r.portal + '" target="_blank" rel="noopener">Open in Portal ↗</a>' : '') + (r.est_url ? ' <a class="btn small" href="' + r.est_url + '">' + esc(r.est) + '</a>' : '') + (r.client_url ? ' <a class="btn small" href="' + r.client_url + '">' + esc(r.client) + '</a>' : '') + '</div></div></div>';
  }

  function sums(rows) {
    const t = {value: 0, budget: 0, gp: 0, exp_gp: 0, exp_gp_pca: 0, n: rows.length, gpv: 0};
    rows.forEach(r => { t.value += r.value || 0; t.budget += r.budget || 0; t.gp += r.gp || 0; t.exp_gp += r.exp_gp || 0; t.exp_gp_pca += r.exp_gp_pca || 0; });
    t.gp_pct = t.value ? t.gp / t.value : null;
    return t;
  }
  function totalsRow(cols, label, t, cls, fadeExp) {
    return '<tr class="' + cls + '"><td></td>' + cols.map((k, i) => {
      const c = COLS[k], tk = c[5];
      let v = '';
      if (i === 0) v = esc(label) + ' <span class="muted" style="font-weight:400">' + t.n + '</span>';
      else if (tk === 'gp_pct') v = pct(t.gp_pct, 1);
      else if (tk) v = money0(t[tk]);
      const fade = fadeExp && (k === 'exp_gp' || k === 'exp_gp_pca');
      return '<td class="' + (i === 0 ? 'fz ' : '') + (c[2] === 'num' ? 'num ' : '') + (fade ? 'faded' : '') + '"' + (fade ? ' title="Expected GP at the time — no longer relevant once decided."' : '') + '>' + v + '</td>';
    }).join('') + '</tr>';
  }

  // rows: [{...}] with optional _sec (section key) — sections render as header + rows + subtotal, all in one table
  function table(host, key, rows, sections) {
    if (!rows.length) { host.innerHTML = '<div class="empty">' + (key === 'window' ? 'Nothing submitted, awarded, lost or due in this window.' : 'Nothing here.') + '</div>'; return {}; }
    const cols = SETS[key];
    // sort prefs are versioned: the quoting default moved to "last activity" and a remembered older sort must not win
    const SORT_PREF = 'ps.sort.v2.' + key;
    let keys = PCA.pref(SORT_PREF, DEFAULT_SORT[key]), sel = PCA.pref('ps.filter.' + key, {}), q = '', only = null;
    if (!Array.isArray(keys) || !keys.length || keys.some(k => !cols.includes(k[0]))) keys = DEFAULT_SORT[key].map(k => k.slice());
    const open = new Set();
    host.innerHTML = '<div class="fbar"><b>' + TITLES[key] + '</b><input type="search" placeholder="search…" data-q>' + FILTERS.map(([k, label]) => '<select data-f="' + k + '" title="' + label + '"></select>').join('') + '<a href="#" class="btn small" data-clear hidden>clear</a><span class="n" data-n></span></div>' +
      '<div class="table-wrap' + (key === 'window' ? ' grow' : '') + '" style="max-height:' + (key === 'open' ? 640 : key === 'window' ? 'none' : 440) + '"><table class="data dense pst"><thead></thead><tbody></tbody></table></div>';
    const rid = r => (r._sec || '') + ':' + r.id;
    const visible = () => rows.filter(r => (!only || only.has(r.id)) && FILTERS.every(([k, , f]) => !sel[k] || (k === 'att' && sel[k] === '__any' ? r.attention.length > 0 : f(r).map(String).includes(sel[k]))) && (!q || [r.name, r.client, r.est, r.type, r.pid, r.rep].join(' ').toLowerCase().includes(q)));
    const sortRows = v => v.sort((a, b) => { for (const [k, dir] of keys) { const c = COLS[k]; if (!c) continue; let x = c[3](a), y = c[3](b); const ex = x == null || x === '', ey = y == null || y === ''; if (ex && ey) continue; if (ex) return 1; if (ey) return -1; const cmp = c[2] === 'text' ? String(x).localeCompare(String(y), undefined, {numeric: true}) : (x < y ? -1 : x > y ? 1 : 0); if (cmp) return dir * cmp; } return 0; });
    function renderFilters() {
      FILTERS.forEach(([k, label, f]) => {
        const by = {}; rows.forEach(r => f(r).forEach(v => { by[v] = (by[v] || 0) + 1; }));
        const ks = Object.keys(by).sort((a, b) => k === 'act' ? ACT_ORDER.indexOf(a) - ACT_ORDER.indexOf(b) : by[b] - by[a]);
        const el = $('select[data-f="' + k + '"]', host);
        if (ks.length < 2) { el.hidden = true; return; }
        el.hidden = false;
        el.innerHTML = '<option value="">' + label + (k === 'att' ? '' : ': all') + '</option>' + (k === 'att' ? '<option value="__any">needs attention (' + rows.filter(r => r.attention.length).length + ')</option>' : '') + ks.map(v => '<option value="' + esc(v) + '"' + (sel[k] === v ? ' selected' : '') + '>' + esc(v) + ' (' + by[v] + ')</option>').join('');
        if (sel[k] === '__any') el.value = '__any';
      });
      $('[data-clear]', host).hidden = !(Object.values(sel).some(Boolean) || only);
    }
    const decided = r => r._sec === 'awarded' || r._sec === 'lost' || r.stage === 'awarded' || r.stage === 'lost';
    const FADE = new Set(['exp_gp', 'exp_gp_pca', 'pca', 'prob']);
    const rowHtml = (r, alt) => '<tr data-rid="' + rid(r) + '"' + (alt ? ' class="alt"' : '') + '><td><span class="caret" title="details: win-rate factors, documents, portal facts">' + (open.has(rid(r)) ? '▾' : '▸') + '</span></td>' +
      cols.map((k, i) => { const c = COLS[k], fade = decided(r) && FADE.has(k); return '<td class="' + (i === 0 ? 'fz ' : '') + (c[2] === 'num' ? 'num ' : '') + (fade ? 'faded' : (c[4] || '')) + '"' + ((c[4] === 'name' || c[4] === 'client' || c[4] === 'est') ? ' title="' + esc(c[3](r)) + '"' : fade ? ' title="Expected value at the time — no longer relevant: this proposal has been ' + (r.stage === 'awarded' || r._sec === 'awarded' ? 'won' : 'lost') + '."' : '') + '>' + c[1](r) + '</td>'; }).join('') + '</tr>' +
      (open.has(rid(r)) ? '<tr class="exp"><td colspan="' + (cols.length + 1) + '">' + detail(r) + '</td></tr>' : '');
    function render() {
      const v = sortRows(visible());
      $('thead', host).innerHTML = '<tr><th style="width:14px" title="Click a row to expand its details: the factors behind PCA\'s win rate, the documents, the portal facts."></th>' + cols.map((k, i) => '<th class="' + (i === 0 ? 'fz ' : '') + (COLS[k][2] === 'num' ? 'num ' : '') + (keys.some(x => x[0] === k) ? 'sorted' : '') + '" data-k="' + k + '" data-dir="' + (keys.some(x => x[0] === k) ? (keys.find(x => x[0] === k)[1] > 0 ? 'asc' : 'desc') : '') + '" style="cursor:pointer" title="' + esc((TIPS[k] || '') + ' Click to sort; shift-click adds a second key.') + '">' + COLS[k][0] + '</th>').join('') + '</tr>';
      let html = '';
      if (sections) {
        sections.forEach(([sk, label]) => {
          const part = v.filter(r => r._sec === sk);
          if (!part.length) return;                       // an empty section is left out entirely
          html += '<tr class="sec s-' + sk + '"><td colspan="' + (cols.length + 1) + '"><span>' + label + ' · ' + part.length + '</span></td></tr>';
          html += part.map((r, i) => rowHtml(r, i % 2 === 1)).join('') + totalsRow(cols, label.replace(' in the window', '') + ' total', sums(part), 'sub', sk === 'awarded' || sk === 'lost');
        });
        if (!html) html = '<tr><td colspan="' + (cols.length + 1) + '" class="empty">Nothing submitted, awarded, lost or due in this window.</td></tr>';
      } else {
        html = v.map((r, i) => rowHtml(r, i % 2 === 1)).join('') + (v.length ? totalsRow(cols, 'Total', sums(v), 'sub') : '<tr><td colspan="' + (cols.length + 1) + '" class="empty">no rows match</td></tr>');
      }
      $('tbody', host).innerHTML = html;
      const t = sums(v);
      $('[data-n]', host).textContent = v.length + ' of ' + rows.length + ' · ' + PCA.money(t.value) + ' value · GP quoted ' + PCA.money(t.gp) + ' · exp. GP ' + PCA.money(t.exp_gp) + ' stated / ' + PCA.money(t.exp_gp_pca) + ' PCA';
      $$('th[data-k]', host).forEach(th => th.addEventListener('click', e => { const k = th.dataset.k, cur = keys.find(x => x[0] === k); if (e.shiftKey) { if (cur) cur[1] = -cur[1]; else keys.push([k, COLS[k][2] === 'text' ? 1 : -1]); } else keys = [[k, cur ? -cur[1] : (COLS[k][2] === 'text' ? 1 : -1)]]; PCA.setPref(SORT_PREF, keys); render(); }));
      $$('tbody tr[data-rid]', host).forEach(tr => tr.addEventListener('click', e => { if (e.target.closest('a')) return; const id = tr.dataset.rid; open.has(id) ? open.delete(id) : open.add(id); render(); }));
    }
    $$('select[data-f]', host).forEach(el => el.addEventListener('change', () => { const k = el.dataset.f; if (el.value) sel[k] = el.value; else delete sel[k]; PCA.setPref('ps.filter.' + key, sel); renderFilters(); render(); }));
    $('[data-clear]', host).addEventListener('click', e => { e.preventDefault(); sel = {}; only = null; PCA.setPref('ps.filter.' + key, sel); renderFilters(); render(); });
    $('[data-q]', host).addEventListener('input', e => { q = e.target.value.trim().toLowerCase(); render(); });
    renderFilters(); render();
    return {setOnly(ids) { only = ids; renderFilters(); render(); }};
  }

  function init() {
    const d = JSON.parse($('#ps-data').textContent);
    const tables = {};
    // the window: four sections in one table
    const win = [].concat(d.window.map(r => Object.assign({}, r, {_sec: 'window'})), d.awarded.map(r => Object.assign({}, r, {_sec: 'awarded'})), d.lost.map(r => Object.assign({}, r, {_sec: 'lost'})), d.due.map(r => Object.assign({}, r, {_sec: 'due'})));
    const hostW = $('#t_window'); if (hostW) tables.window = table(hostW, 'window', win, SECTIONS);
    $$('[data-table]').forEach(el => { if (el.dataset.table !== 'window') tables[el.dataset.table] = table(el, el.dataset.table, d[el.dataset.table] || []); });
    // the group rows narrow the window table to one division / estimator
    $$('tr.drillgrp').forEach(tr => tr.addEventListener('click', e => { if (e.target.closest('a')) return; const ids = new Set(tr.dataset.ids.split(',').filter(Boolean).map(Number)); const on = tr.classList.toggle('rowgood'); $$('tr.drillgrp').forEach(x => { if (x !== tr) x.classList.remove('rowgood'); }); tables.window && tables.window.setOnly && tables.window.setOnly(on ? ids : null); }));
    const c = $('#c_strip');
    if (c && d.strip && d.strip.length && typeof Chart !== 'undefined') {
      new Chart(c, {type: 'bar', data: {labels: d.strip.map(x => x.k), datasets: [{data: d.strip.map(x => x.v), backgroundColor: d.strip.map(x => (x.k >= d.start && x.k <= d.end) ? '#1f5eff' : '#c9d4e5')}]},
        options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: ctx => money0(ctx.raw) + ' · ' + d.strip[ctx.dataIndex].n + ' proposal' + (d.strip[ctx.dataIndex].n === 1 ? '' : 's')}}}, scales: {x: {display: false}, y: {display: false}}}});
    }
  }
  return {init};
})();
