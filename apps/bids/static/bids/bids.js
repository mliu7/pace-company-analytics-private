// Bids pages: overview charts + drills, the board, the calendar, the full-data list, the analytics charts.
// Chart.js, PCA (money/pct/pref/setPref/colors) and PCADrill come from base.html / finance_modals.js.
window.PCABids = (function () {
  const $ = (s, r) => (r || document).querySelector(s), $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const money0 = v => v == null ? '—' : v.toLocaleString('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 0});
  const json = id => { const el = document.getElementById(id); return el ? JSON.parse(el.textContent || 'null') : null; };
  const FILTERS = () => json('bids-filters-json') || {};
  const STAGE_CLASS = {quoting: 'warn', submitted: 'info', awarded: 'good', lost: 'bad'};
  const pill = r => '<span class="pill ' + (STAGE_CLASS[r.stage] || '') + '">' + esc(r.stage_label) + '</span>';
  const link = r => '<a href="/bids/' + r.id + '/">' + esc(r.name || r.num || '(untitled)') + '</a>';
  const fmtDate = s => s ? new Date(s + 'T00:00:00').toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: '2-digit'}) : '—';

  // PCA.qs({k: v}) → the current querystring with those keys replaced (used by the pivot axis selectors)
  if (typeof PCA !== 'undefined' && !PCA.qs) PCA.qs = patch => { const q = new URLSearchParams(location.search); for (const k in patch) { if (patch[k] === null || patch[k] === '') q.delete(k); else q.set(k, patch[k]); } return '?' + q.toString(); };

  // stage chips in the filter bar toggle their hidden checkbox
  $$('.stage-chips .chip').forEach(ch => ch.addEventListener('click', e => { e.preventDefault(); const cb = ch.querySelector('input'); cb.checked = !cb.checked; ch.classList.toggle('on', cb.checked); }));

  // ---- drills: any .drill element opens PCADrill with the page's filters + its data-* keys
  function bindDrills(url) {
    if (typeof PCADrill === 'undefined') return;
    PCADrill.bind(url);
    document.addEventListener('click', e => {
      const el = e.target.closest('.drill'); if (!el || el.closest('#drillmodal')) return;
      e.preventDefault(); e.stopImmediatePropagation();
      const q = Object.assign({}, FILTERS(), {kind: el.dataset.kind || 'all', bucket: el.dataset.bucket || '', axis: el.dataset.axis || '', month: el.dataset.month || ''});
      PCADrill.open({title: el.dataset.title || 'Bids', q: q});
    }, true);
  }
  function drill(url, title, q) { if (typeof PCADrill === 'undefined') return; PCADrill.open({title, q: Object.assign({}, FILTERS(), q)}); }

  const barOpts = (onClick, fmt) => ({responsive: true, maintainAspectRatio: false, onClick, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => (fmt || money0)(c.raw)}}},
    scales: {x: {grid: {display: false}}, y: {ticks: {callback: v => PCA.money(v)}}}});

  // ---- overview
  function overview(url) {
    bindDrills(url);
    const d = json('bids-charts'); if (!d) return;
    const stages = ['quoting', 'submitted', 'on_hold', 'awarded', 'lost', 'did_not_bid', 'no_decision'];
    const labels = {quoting: 'Quoting', submitted: 'Submitted', on_hold: 'On hold', awarded: 'Awarded', lost: 'Lost', did_not_bid: 'Did not bid', no_decision: 'No decision'};
    const fun = stages.filter(s => d.funnel[s] && d.funnel[s].n);
    new Chart($('#c_funnel'), {type: 'bar', data: {labels: fun.map(s => labels[s]), datasets: [{data: fun.map(s => d.funnel[s].value), backgroundColor: fun.map((s, i) => PCA.colors[i % PCA.colors.length])}]},
      options: Object.assign(barOpts((e, els) => { if (els.length) drill(url, labels[fun[els[0].index]], {kind: 'stage', bucket: fun[els[0].index]}); }), {plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => money0(c.raw) + ' · ' + d.funnel[fun[c.dataIndex]].n + ' bids'}}}})});
    new Chart($('#c_bidder'), {type: 'bar', data: {labels: d.by_bidder.map(x => x.key), datasets: [{data: d.by_bidder.map(x => x.value), backgroundColor: PCA.colors[0]}]},
      options: Object.assign(barOpts((e, els) => { if (els.length) drill(url, d.by_bidder[els[0].index].key, {kind: 'bidder', bucket: d.by_bidder[els[0].index].key}); }), {indexAxis: 'y', scales: {x: {ticks: {callback: v => PCA.money(v)}}, y: {grid: {display: false}}}, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => money0(c.raw) + ' · ' + d.by_bidder[c.dataIndex].n + ' bids'}}}})});
    new Chart($('#c_prob'), {type: 'bar', data: {labels: d.by_probability.map(x => x.key), datasets: [{data: d.by_probability.map(x => x.value), backgroundColor: PCA.colors[1]}]},
      options: Object.assign(barOpts((e, els) => { if (els.length) drill(url, 'Probability ' + d.by_probability[els[0].index].key, {kind: 'prob', bucket: d.by_probability[els[0].index].key}); }), {plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => money0(c.raw) + ' · ' + d.by_probability[c.dataIndex].n + ' bids'}}}})});
    winTrend($('#c_trend'), d.win_trend, url);
  }
  function winTrend(canvas, t, url) {
    if (!canvas || !t) return;
    new Chart(canvas, {type: 'bar', data: {labels: t.map(x => x.q), datasets: [
        {type: 'line', label: 'Hit rate (count)', data: t.map(x => x.hit == null ? null : x.hit * 100), borderColor: PCA.colors[0], backgroundColor: PCA.colors[0], yAxisID: 'y1', tension: .3},
        {type: 'line', label: 'Hit rate ($)', data: t.map(x => x.hit_w == null ? null : x.hit_w * 100), borderColor: PCA.colors[2], backgroundColor: PCA.colors[2], yAxisID: 'y1', tension: .3, borderDash: [4, 3]},
        {label: 'Won', data: t.map(x => x.won), backgroundColor: PCA.colors[1], stack: 'n'}, {label: 'Lost', data: t.map(x => x.lost), backgroundColor: '#d9dee6', stack: 'n'}]},
      options: {responsive: true, maintainAspectRatio: false, onClick: (e, els) => { if (els.length) drill(url, t[els[0].index].q, {kind: 'quarter', bucket: t[els[0].index].q}); },
        scales: {x: {grid: {display: false}, stacked: true}, y: {stacked: true, title: {display: true, text: 'bids'}}, y1: {position: 'right', min: 0, max: 100, grid: {display: false}, ticks: {callback: v => v + '%'}}},
        plugins: {legend: {position: 'bottom'}}}});
  }

  // ---- board
  function board() {
    const rows = json('bids-rows') || [], sortSel = $('#board_sort'), grp = $('#board_group');
    sortSel.value = PCA.pref('board.sort', 'edited'); grp.checked = !!PCA.pref('board.group', false);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const cmp = {edited: (a, b) => (b.modified || '') < (a.modified || '') ? -1 : 1, due: (a, b) => (a.due || '9') < (b.due || '9') ? -1 : 1, value: (a, b) => (b.value || 0) - (a.value || 0), prob: (a, b) => (b.prob || -1) - (a.prob || -1), days: (a, b) => (b.days || 0) - (a.days || 0), client: (a, b) => (a.client || '').localeCompare(b.client || '')};
    const card = r => {
      const due = r.due ? new Date(r.due + 'T00:00:00') : null, soon = due && !r.overdue && (due - today) / 864e5 <= 7;
      return '<a class="bcard' + (r.overdue ? ' overdue' : soon ? ' soon' : '') + '" href="/bids/' + r.id + '/" title="' + esc(r.name) + (r.status_raw ? ' · ' + esc(r.status_raw) : '') + '">' +
        '<div class="t">' + esc(r.name || r.num || '(untitled)') + '</div>' +
        '<div class="m"><span class="v">' + (r.value == null ? '—' : PCA.money(r.value)) + '</span>' + (r.prob != null ? '<span class="prob" title="' + r.prob + '% stated"><i style="width:' + r.prob + '%"></i></span>' : '<span class="muted">unscored</span>') + (r.client ? '<span>' + esc(r.client) + '</span>' : '') + '</div>' +
        '<div class="m">' + (r.est ? '<span>' + esc(r.est) + '</span>' : '<span class="neg">no bidder</span>') + (r.due ? '<span' + (r.overdue ? ' class="neg"' : '') + '>due ' + fmtDate(r.due) + '</span>' : '') + (r.days != null ? '<span title="days since the portal row was last edited">' + r.days + 'd since edit</span>' : '') + (r.div ? '<span>' + r.div + '</span>' : '') + (r.bom && r.bom.toUpperCase() === 'NEEDED' ? '<span class="tag warn">BOM</span>' : '') + '</div></a>';
    };
    function render() {
      const k = sortSel.value, g = grp.checked;
      PCA.setPref('board.sort', k); PCA.setPref('board.group', g);
      $$('.col', $('#board')).forEach(col => {
        const items = rows.filter(r => r.stage === col.dataset.stage).sort(cmp[k] || cmp.due);
        $('.colsum', col).textContent = PCA.money(items.reduce((a, r) => a + (r.value || 0), 0));
        let html = '';
        if (g) { const by = {}; items.forEach(r => (by[r.est || '(no bidder)'] = by[r.est || '(no bidder)'] || []).push(r));
          Object.keys(by).sort().forEach(e => { html += '<div class="sub-h">' + esc(e) + ' <span class="muted">' + by[e].length + ' · ' + PCA.money(by[e].reduce((a, r) => a + (r.value || 0), 0)) + '</span></div>' + by[e].map(card).join(''); }); }
        else html = items.map(card).join('');
        $('.cards', col).innerHTML = html || '<div class="empty">none</div>';
      });
    }
    sortSel.addEventListener('change', render); grp.addEventListener('change', render); render();
  }

  // ---- calendar
  function calendar(url) {
    let cur = PCA.pref('cal.month'); cur = cur ? new Date(cur + '-01T00:00:00') : new Date(); cur.setDate(1);
    let off = new Set(PCA.pref('cal.off', []));
    const legend = $('#cal_legend');
    legend.querySelectorAll('.ev').forEach(el => { el.classList.toggle('off', off.has(el.dataset.kind)); el.addEventListener('click', () => { off.has(el.dataset.kind) ? off.delete(el.dataset.kind) : off.add(el.dataset.kind); el.classList.toggle('off'); PCA.setPref('cal.off', [...off]); load(); }); });
    const KIND = {due: 'Bid due', submitted: 'Submitted', awarded: 'Awarded', walkthrough: 'Walkthrough', start: 'Project start'};
    function load() {
      const y = cur.getFullYear(), m = cur.getMonth();
      PCA.setPref('cal.month', y + '-' + String(m + 1).padStart(2, '0'));
      $('#cal_title').textContent = cur.toLocaleDateString('en-US', {month: 'long', year: 'numeric'});
      const first = new Date(y, m, 1), gridStart = new Date(first); gridStart.setDate(1 - first.getDay());
      const gridEnd = new Date(gridStart); gridEnd.setDate(gridStart.getDate() + 41);
      const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
      const q = new URLSearchParams(); const F = FILTERS(); for (const k in F) { if (Array.isArray(F[k])) F[k].forEach(v => q.append(k, v)); else if (F[k]) q.set(k, F[k]); }
      q.set('start', iso(gridStart)); q.set('end', iso(gridEnd));
      fetch(url + '?' + q).then(r => r.json()).then(res => {
        const ev = res.events.filter(e => !off.has(e.kind)), byDay = {};
        ev.forEach(e => (byDay[e.date] = byDay[e.date] || []).push(e));
        const todayIso = iso(new Date());
        let html = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(d => '<div class="dh">' + d + '</div>').join('');
        for (let i = 0; i < 42; i++) {
          const d = new Date(gridStart); d.setDate(gridStart.getDate() + i); const k = iso(d);
          const items = (byDay[k] || []).sort((a, b) => (b.value || 0) - (a.value || 0));
          const evHtml = e => '<a class="ev ' + e.kind + '" href="/bids/' + e.id + '/" title="' + esc(KIND[e.kind] + ': ' + e.name + (e.client ? ' · ' + e.client : '') + (e.value != null ? ' · ' + money0(e.value) : '') + (e.est ? ' · ' + e.est : '')) + '">' + esc(e.name || e.num) + '</a>';
          const MAXV = 7, shown = items.slice(0, MAXV), hidden = items.slice(MAXV);
          html += '<div class="day' + (d.getMonth() !== m ? ' other' : '') + (k === todayIso ? ' today' : '') + '"><div class="n">' + d.getDate() + (items.length ? ' <span class="muted">' + items.length + ' · ' + PCA.money(items.reduce((a, e) => a + (e.value || 0), 0)) + '</span>' : '') + '</div>' +
            shown.map(evHtml).join('') + (hidden.length ? '<span class="more" data-more>+' + hidden.length + ' more</span><span hidden>' + hidden.map(evHtml).join('') + '</span>' : '') + '</div>';
        }
        $('#cal').innerHTML = html;
        $$('#cal [data-more]').forEach(el => el.addEventListener('click', () => { el.nextElementSibling.hidden = false; el.remove(); }));
        const month = ev.filter(e => e.date.slice(0, 7) === y + '-' + String(m + 1).padStart(2, '0')).sort((a, b) => a.date < b.date ? -1 : 1);
        $('#cal_n').textContent = month.length + ' events · ' + PCA.money(month.reduce((a, e) => a + (e.value || 0), 0));
        $('#cal_list tbody').innerHTML = month.map(e => '<tr><td class="nowrap">' + fmtDate(e.date) + '</td><td><span class="ev ' + e.kind + '" style="display:inline-block;padding:0 6px;border-radius:4px">' + KIND[e.kind] + '</span></td><td>' + link(e) + '</td><td class="trunc" title="' + esc(e.client) + '">' + esc(e.client) + '</td><td>' + pill(e) + '</td><td class="num">' + (e.value == null ? '—' : money0(e.value)) + '</td><td>' + esc(e.est) + '</td><td>' + esc(e.div) + '</td></tr>').join('') || '<tr><td colspan="8" class="empty">nothing this month</td></tr>';
      });
    }
    $('#cal_prev').addEventListener('click', e => { e.preventDefault(); cur.setMonth(cur.getMonth() - 1); load(); });
    $('#cal_next').addEventListener('click', e => { e.preventDefault(); cur.setMonth(cur.getMonth() + 1); load(); });
    $('#cal_today').addEventListener('click', e => { e.preventDefault(); cur = new Date(); cur.setDate(1); load(); });
    load();
  }

  // ---- list (full data, facets, multi-key sort, column sets)
  const COLS = {
    num: ['Job #', r => r.num ? (r.job ? '<a href="/projects/' + r.cpn + '/">' + esc(r.num) + '</a>' : esc(r.num)) : '<span class="muted">' + esc(r.pid) + '</span>', 'text', r => r.num || r.pid],
    name: ['Project', r => link(r), 'text', r => r.name, 'trunc'], client: ['Client', r => esc(r.client), 'text', r => r.client, 'trunc'],
    stage: ['Stage', r => pill(r), 'text', r => r.stage_label], div: ['Div', r => esc(r.div), 'text', r => r.div], est: ['Estimator', r => esc(r.est) + (r.inferred ? ' <span class="tag" title="blank Bidder, credited by rule">inf</span>' : ''), 'text', r => r.est],
    rep: ['Rep', r => esc(r.rep), 'text', r => r.rep], due: ['Bid due', r => fmtDate(r.due), 'date', r => r.due], submitted: ['Submitted', r => fmtDate(r.submitted), 'date', r => r.submitted],
    awarded: ['Awarded', r => fmtDate(r.awarded), 'date', r => r.awarded], value: ['Value', r => r.value == null ? '—' : money0(r.value), 'num', r => r.value],
    budget: ['Budget', r => r.budget == null ? '—' : money0(r.budget), 'num', r => r.budget], margin: ['Margin', r => r.margin == null ? '—' : PCA.pct(r.margin), 'num', r => r.margin],
    prob: ['Prob', r => r.prob == null ? '—' : r.prob + '%', 'num', r => r.prob], bom: ['BOM', r => esc(r.bom), 'text', r => r.bom], ball: ['Ball in court', r => esc(r.ball), 'text', r => r.ball],
    walk: ['Walkthrough', r => esc(r.walk), 'text', r => r.walk], status_raw: ['Portal status', r => esc(r.status_raw), 'text', r => r.status_raw],
    cv: ['SL contract', r => r.cv == null ? '—' : money0(r.cv), 'num', r => r.cv], gp: ['Final GP', r => r.gp == null ? '—' : PCA.pct(r.gp), 'num', r => r.gp],
    acc: ['Accuracy', r => r.acc == null ? '—' : '<span class="' + (r.acc >= 0 ? 'pos' : 'neg') + '">' + (r.acc >= 0 ? '+' : '−') + Math.abs(r.acc * 100).toFixed(1) + ' pts</span>', 'num', r => r.acc],
    pstate: ['SL state', r => esc(r.pstate), 'text', r => r.pstate], won: ['Won', r => r.won ? '✓' + (r.won_by_sl && r.stage !== 'awarded' ? ' <span class="muted" title="per SL billings">SL</span>' : '') : '', 'text', r => r.won ? 'yes' : 'no'],
    modified: ['Modified', r => fmtDate(r.modified && r.modified.slice(0, 10)), 'date', r => r.modified], src: ['Source', r => esc(r.src), 'text', r => r.src], year: ['Year', r => r.year, 'num', r => r.year], size: ['Size', r => esc(r.size), 'text', r => r.size],
  };
  const SETS = {std: ['num', 'name', 'client', 'stage', 'div', 'est', 'rep', 'due', 'submitted', 'value', 'margin', 'prob'],
    outcome: ['num', 'name', 'client', 'stage', 'est', 'value', 'budget', 'margin', 'awarded', 'cv', 'gp', 'acc', 'pstate'],
    portal: ['num', 'name', 'client', 'status_raw', 'est', 'rep', 'due', 'bom', 'ball', 'walk', 'prob', 'modified', 'src'],
    all: Object.keys(COLS)};
  function list(url) {
    let rows = [], keys = PCA.pref('list.sort', [['due', -1]]), facets = PCA.pref('list.facets', {}), q = '', set = PCA.pref('list.cols', 'std');
    $('#list_cols').value = set;
    const FACETS = [['stage', 'stage_label'], ['div', 'div'], ['est', 'est'], ['year', 'year'], ['size', 'size'], ['rep', 'rep'], ['bom', 'bom']];
    const cols = () => SETS[set] || SETS.std;
    function visible() {
      const t = q.toLowerCase();
      return rows.filter(r => FACETS.every(([k, f]) => !facets[k] || !facets[k].length || facets[k].includes(String(r[f] || '(blank)'))) && (!t || [r.name, r.client, r.num, r.est, r.rep, r.status_raw, r.pid].join(' ').toLowerCase().includes(t)));
    }
    function sortRows(v) {
      const ks = keys.filter(k => COLS[k[0]]);
      return v.sort((a, b) => { for (const [k, dir] of ks) { const c = COLS[k]; let x = c[3](a), y = c[3](b); const ex = x == null || x === '', ey = y == null || y === ''; if (ex && ey) continue; if (ex) return 1; if (ey) return -1; const r = c[2] === 'num' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true}); if (r) return dir * r; } return 0; });
    }
    function renderFacets() {
      const v = visible(); let html = '';
      FACETS.forEach(([k, f]) => {
        const by = {}; rows.forEach(r => { const key = String(r[f] || '(blank)'); by[key] = (by[key] || 0) + 1; });
        const keysF = Object.keys(by).sort((a, b) => by[b] - by[a]).slice(0, k === 'est' || k === 'rep' ? 14 : 12);
        if (keysF.length < 2) return;
        html += '<span class="muted" style="margin-left:6px">' + k + ':</span>' + keysF.map(key => '<span class="chip' + ((facets[k] || []).includes(key) ? ' on' : '') + '" data-f="' + k + '" data-v="' + esc(key) + '">' + esc(key) + ' <small>' + by[key] + '</small></span>').join('');
      });
      $('#list_facets').innerHTML = html + (Object.values(facets).some(a => a && a.length) ? '<span class="chip" data-clear="1">✕ clear</span>' : '');
    }
    function render() {
      const cs = cols(), v = sortRows(visible());
      $('#list thead').innerHTML = '<tr>' + cs.map((k, i) => '<th class="' + (i === 0 ? 'fz ' : '') + (COLS[k][2] === 'num' ? 'num ' : '') + (keys.some(x => x[0] === k) ? 'sorted' : '') + '" data-k="' + k + '" data-dir="' + (keys.some(x => x[0] === k) ? (keys.find(x => x[0] === k)[1] > 0 ? 'asc' : 'desc') : '') + '" style="cursor:pointer">' + COLS[k][0] + '</th>').join('') + '</tr>';
      $('#list tbody').innerHTML = v.map(r => '<tr>' + cs.map((k, i) => '<td class="' + (i === 0 ? 'fz ' : '') + (COLS[k][2] === 'num' ? 'num' : COLS[k][4] || 'nowrap') + '"' + (COLS[k][4] === 'trunc' ? ' title="' + esc(COLS[k][3](r)) + '"' : '') + '>' + COLS[k][1](r) + '</td>').join('') + '</tr>').join('') || '<tr><td colspan="' + cs.length + '" class="empty">no rows</td></tr>';
      $('#list_n').textContent = v.length.toLocaleString() + ' of ' + rows.length.toLocaleString() + ' bids · ' + PCA.money(v.reduce((a, r) => a + (r.value || 0), 0));
      $$('#list th').forEach(th => th.addEventListener('click', e => { const k = th.dataset.k, cur = keys.find(x => x[0] === k); if (e.shiftKey) { if (cur) cur[1] = -cur[1]; else keys.push([k, COLS[k][2] === 'text' ? 1 : -1]); } else keys = [[k, cur ? -cur[1] : (COLS[k][2] === 'text' ? 1 : -1)]]; PCA.setPref('list.sort', keys); render(); }));
    }
    $('#list_facets').addEventListener('click', e => { const ch = e.target.closest('.chip'); if (!ch) return; if (ch.dataset.clear) facets = {}; else { const a = facets[ch.dataset.f] = facets[ch.dataset.f] || []; const i = a.indexOf(ch.dataset.v); i >= 0 ? a.splice(i, 1) : a.push(ch.dataset.v); } PCA.setPref('list.facets', facets); renderFacets(); render(); });
    $('#list_q').addEventListener('input', e => { q = e.target.value; render(); });
    $('#list_cols').addEventListener('change', e => { set = e.target.value; PCA.setPref('list.cols', set); render(); });
    const qs = new URLSearchParams(); const F = FILTERS(); for (const k in F) { if (Array.isArray(F[k])) F[k].forEach(v => qs.append(k, v)); else if (F[k]) qs.set(k, F[k]); }
    fetch(url + '?' + qs).then(r => r.json()).then(res => { rows = res.rows; renderFacets(); render(); });
  }

  // ---- analytics
  function analytics(url) {
    bindDrills(url);
    const d = json('bids-charts'); if (!d) return;
    // pipeline history from the daily snapshots
    const days = [...new Set(d.hist.map(h => h.d))].sort();
    const series = st => days.map(day => { const h = d.hist.find(x => x.d === day && x.stage === st); return h ? h.value : 0; });
    const wtd = days.map(day => d.hist.filter(x => x.d === day).reduce((a, h) => a + h.weighted, 0));
    if (days.length) new Chart($('#c_hist'), {type: 'line', data: {labels: days, datasets: [
        {label: 'Quoting', data: series('quoting'), borderColor: PCA.colors[2], backgroundColor: PCA.colors[2] + '33', fill: true, stack: 's', tension: .2, pointRadius: 0},
        {label: 'Submitted', data: series('submitted'), borderColor: PCA.colors[0], backgroundColor: PCA.colors[0] + '33', fill: true, stack: 's', tension: .2, pointRadius: 0},
        {label: 'Weighted', data: wtd, borderColor: PCA.colors[1], borderDash: [4, 3], pointRadius: 0, tension: .2}]},
      options: {responsive: true, maintainAspectRatio: false, scales: {y: {stacked: false, ticks: {callback: v => PCA.money(v)}}, x: {ticks: {maxTicksLimit: 8}}}, plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + money0(c.raw)}}}}});
    else $('#c_hist').parentElement.innerHTML = '<div class="empty">Snapshots start accumulating with the next scheduled refresh.</div>';
    winTrend($('#c_trend'), d.trend, url);
    new Chart($('#c_calib'), {type: 'bar', data: {labels: d.calib.map(c => c.bucket + '%'), datasets: [{label: 'Stated', data: d.calib.map(c => c.bucket), backgroundColor: '#d9dee6'}, {label: 'Actual win rate', data: d.calib.map(c => c.actual == null ? null : c.actual * 100), backgroundColor: PCA.colors[0]}]},
      options: {responsive: true, maintainAspectRatio: false, onClick: (e, els) => { if (els.length) drill(url, 'Stated ' + d.calib[els[0].index].bucket + '%', {kind: 'calib', bucket: d.calib[els[0].index].bucket}); }, scales: {y: {min: 0, max: 100, ticks: {callback: v => v + '%'}}, x: {grid: {display: false}}}, plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {label: c => c.dataset.label + ': ' + (c.raw == null ? '—' : c.raw.toFixed(0) + '%') + (c.datasetIndex ? ' · n=' + d.calib[c.dataIndex].n : '')}}}}});
    new Chart($('#c_cycle'), {type: 'bar', data: {labels: d.cycle.map(x => x[0] + (x[0] >= 180 ? '+' : '–' + (x[0] + 14)) + 'd'), datasets: [{data: d.cycle.map(x => x[1]), backgroundColor: PCA.colors[4]}]},
      options: {responsive: true, maintainAspectRatio: false, onClick: (e, els) => { if (els.length) drill(url, 'Cycle ' + d.cycle[els[0].index][0] + 'd bucket', {kind: 'cycle', bucket: d.cycle[els[0].index][0]}); }, scales: {x: {grid: {display: false}}, y: {title: {display: true, text: 'bids'}}}, plugins: {legend: {display: false}}}});
    new Chart($('#c_drift'), {type: 'scatter', data: {datasets: [{data: d.drift, backgroundColor: PCA.colors[0] + 'aa', pointRadius: 4}, {type: 'line', data: (() => { const m = Math.max(...d.drift.map(p => Math.max(p.x, p.y)), 1); return [{x: 0, y: 0}, {x: m, y: m}]; })(), borderColor: '#c3c9d2', borderDash: [4, 4], pointRadius: 0}]},
      options: {responsive: true, maintainAspectRatio: false, onClick: (e, els) => { const p = els.find(x => x.datasetIndex === 0); if (p) window.open('/bids/' + d.drift[p.index].id + '/', '_blank'); },
        scales: {x: {type: 'logarithmic', title: {display: true, text: 'bid value'}, ticks: {callback: v => PCA.money(v)}}, y: {type: 'logarithmic', title: {display: true, text: 'SL contract value'}, ticks: {callback: v => PCA.money(v)}}},
        plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => c.datasetIndex ? '' : d.drift[c.dataIndex].name + ': bid ' + money0(c.raw.x) + ' → SL ' + money0(c.raw.y)}}}}});
    new Chart($('#c_pct'), {type: 'bar', data: {labels: d.pct.map(x => x.key), datasets: [{data: d.pct.map(x => x.value), backgroundColor: PCA.colors[1]}]},
      options: Object.assign(barOpts((e, els) => { if (els.length) drill(url, 'Stated ' + d.pct[els[0].index].key, {kind: 'prob', bucket: d.pct[els[0].index].key}); }), {plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => money0(c.raw) + ' · ' + d.pct[c.dataIndex].n + ' bids'}}}})});
  }

  return {overview, board, calendar, list, analytics};
})();
