/* Active tasks (spec §6.2, AT-01…AT-08): a live view over the status board. Priority moves with the clock; filters
   are remembered; each chart is aggregated from every filter except its own facet; PM filter matches PM1 or PM2. */
(function () {
  const DS = JSON.parse(document.getElementById('at-data').textContent), C = window.AT_CONF, V = DS.vocab, esc = PL.esc, $ = s => document.querySelector(s);
  const ALL = DS.rows.filter(r => !r.completed && r.percent < 100);
  const divisions = DS.divisions;
  let div = (C.init.div && divisions.includes(C.init.div)) ? C.init.div : PL.pref('div', divisions.includes('070') ? '070' : divisions[0]);
  let fPM = C.init.pm || PL.pref('pm', 'ALL'), fPri = C.init.priority || 'ALL', fSt = 'ALL', fMonth = 'ALL', fStart = false, q = '', sort = PL.pref('sort', {k: 'end', dir: 1});
  const charts = {};
  const today = () => PL.todayISO();
  const pri = r => PL.priority(r.end, today());
  const isBlank = s => !s;
  const stKey = r => { const a = r.phase_status || '', b = r.equipment_status || ''; return (!a && !b) ? ['None'] : [...new Set([a, b].filter(Boolean))]; };
  const projKey = r => r.project_number || r.job_key || r.name;
  const pmMatch = r => fPM === 'ALL' || [r.pm_label, r.pm2_label].map(x => (x || '').toUpperCase()).includes(fPM.toUpperCase());
  const monthKey = r => r.end ? r.end.slice(0, 7) : 'No date';
  function base(exclude) {
    return ALL.filter(r => r.division === div)
      .filter(r => exclude === 'pm' || pmMatch(r))
      .filter(r => exclude === 'priority' || fPri === 'ALL' || pri(r) === fPri)
      .filter(r => exclude === 'status' || fSt === 'ALL' || stKey(r).includes(fSt))
      .filter(r => exclude === 'month' || fMonth === 'ALL' || monthKey(r) === fMonth)
      .filter(r => !fStart || PL.startingSoon(r.start, today()))
      .filter(r => !q || (r.name + ' ' + r.pm_label + ' ' + r.pm2_label + ' ' + stKey(r).join(' ') + ' ' + (r.project_number || '')).toLowerCase().includes(q));
  }
  function summary(rows) {
    const s = {active: rows.length, hours: 0, soon: 0, red: 0, yellow: 0, none: 0, urgent: 0, pct: 0};
    rows.forEach(r => { s.hours += r.hours_left; const p = pri(r); if (p === 'red') s.red++; if (p === 'yellow') s.yellow++; if (p === 'none') s.none++; if (p === 'red' || p === 'yellow') s.urgent += r.hours_left; if (PL.startingSoon(r.start, today())) s.soon++; s.pct += r.percent; });
    s.avg = rows.length ? Math.round(s.pct / rows.length) : 0; return s;
  }
  function render() {
    const rows = base();
    $('#at-div').innerHTML = divisions.map(d => '<button data-d="' + d + '" class="' + (d === div ? 'active' : '') + '">' + d + ' <span class="n" title="active tasks">' + ALL.filter(r => r.division === d).length + '</span></button>').join('');
    $('#at-divlabel').textContent = div;
    // sidebar PM list (PM1 counts + union-hour meter over the unfiltered division set; PM2 matches too)
    const divAll = ALL.filter(r => r.division === div), hrs = {}, cnt = {};
    divAll.forEach(r => { const k = r.pm_label || '-'; hrs[k] = (hrs[k] || 0) + r.hours_left; cnt[k] = (cnt[k] || 0) + 1; if (r.pm2_label) { cnt[r.pm2_label] = (cnt[r.pm2_label] || 0) + 0; hrs[r.pm2_label] = hrs[r.pm2_label] || 0; } });
    const maxH = Math.max(1, ...Object.values(hrs));
    const pms = Object.keys(cnt).sort((a, b) => (hrs[b] - hrs[a]) || a.localeCompare(b));
    $('#at-pms').innerHTML = '<button data-pm="ALL" class="' + (fPM === 'ALL' ? 'active' : '') + '"><span class="dot" style="background:#8a94a3"></span><span>All PMs</span><span class="n">' + divAll.length + '</span></button>' +
      pms.map(p => '<button data-pm="' + esc(p) + '" class="' + (fPM.toUpperCase() === p.toUpperCase() ? 'active' : '') + '" title="' + esc(p) + ': ' + cnt[p] + ' tasks as PM1 · ' + PL.fmtNum(hrs[p]) + ' union hrs"><span class="dot" style="background:' + PL.pmColor(p) + '"></span><span>' + esc(p) + '</span><span class="n">' + cnt[p] + '</span><span class="meter"><span style="width:' + Math.max(4, Math.round(100 * hrs[p] / maxH)) + '%"></span></span></button>').join('');
    const pb = base('priority');
    $('#at-pri').innerHTML = [['ALL', 'All'], ['red', 'Past due'], ['yellow', 'Due ≤ 14 days'], ['green', 'On track'], ['none', 'No date']].map(([k, l]) => '<button data-pri="' + k + '" class="' + (fPri === k ? 'active' : '') + '"><span class="dot" style="background:' + (PL.PRIORITY_COLORS[k] || '#8a94a3') + '"></span><span>' + l + '</span><span class="n">' + (k === 'ALL' ? pb.length : pb.filter(r => pri(r) === k).length) + '</span></button>').join('');
    const sb = base('status'), stc = {};
    sb.forEach(r => stKey(r).forEach(s => { (stc[s] = stc[s] || new Set()).add(projKey(r)); }));
    const sts = Object.keys(stc).sort((a, b) => (a === 'None' ? -1 : b === 'None' ? 1 : stc[b].size - stc[a].size || a.localeCompare(b)));
    $('#at-st').innerHTML = '<button data-st="ALL" class="' + (fSt === 'ALL' ? 'active' : '') + '"><span></span><span>All statuses</span><span class="n">' + sb.length + '</span></button>' + sts.map(s => '<button data-st="' + esc(s) + '" class="' + (fSt === s ? 'active' : '') + '" title="' + stc[s].size + ' distinct projects"><span class="dot" style="background:' + ((V.colors[s] || ['#eef2f7'])[0]) + ';border:1px solid #cbd3de"></span><span>' + esc(s) + '</span><span class="n">' + stc[s].size + '</span></button>').join('');
    // focus bar
    const fb = $('#at-focus');
    if (fPM !== 'ALL') { const s = summary(rows), all = summary(ALL.filter(pmMatch)); fb.hidden = false;
      fb.innerHTML = '<b>' + esc(fPM) + '</b> <span>This schedule <b>' + s.active + '</b> tasks · <b>' + PL.fmtNum(s.hours) + '</b> union hrs · <b>' + s.red + '</b> past due</span><span class="muted">All schedules ' + all.active + ' tasks / ' + PL.fmtNum(all.hours) + ' hrs</span><span class="spacer"></span><a class="btn small" href="' + C.reportUrl + '?pm=' + encodeURIComponent(fPM) + '">Print PM report</a><a class="btn small" href="' + C.exportUrl + '?pm=' + encodeURIComponent(fPM) + '">Export PM report</a><button class="btn small" id="at-clearpm">Clear PM</button>';
      $('#at-print').href = C.reportUrl + '?pm=' + encodeURIComponent(fPM); $('#at-xlsx').href = C.exportUrl + '?pm=' + encodeURIComponent(fPM);
    } else { fb.hidden = true; $('#at-print').href = C.reportUrl; $('#at-xlsx').href = C.exportUrl; }
    // KPI tiles (click-to-filter)
    const s = summary(rows);
    const tile = (k, l, v, sub, cls, on, tip) => '<div class="kpi card tight click ' + cls + (on ? ' on' : '') + '" data-k="' + k + '" title="' + esc(tip) + '"><div class="label">' + l + '</div><div class="value">' + v + '</div><div class="sub">' + sub + '</div></div>';
    $('#at-kpi').innerHTML = tile('all', 'Active tasks', s.active, 'not completed, < 100 %', '', false, 'Tasks passing the current filters') +
      tile('hours', 'Total union hrs', PL.fmtNum(s.hours), 'remaining', '', false, 'Σ union hours remaining') +
      tile('start', 'Starting soon', s.soon, 'start within 14 days', 'green', fStart, 'Start date today … +14 days (click to filter)') +
      tile('red', 'Past critical stop', s.red, 'stop date before today', 'red', fPri === 'red', 'Critical stop before today (click to filter)') +
      tile('none', 'Missing stop date', s.none, 'no critical stop', 'amber', fPri === 'none', 'No critical stop date (click to filter)') +
      tile('urgent', 'Urgent union hrs', PL.fmtNum(s.urgent), 'past due + ≤ 14 days', 'red', false, 'Σ union hours of past-due and due-soon tasks');
    renderCharts(); renderTable(rows);
  }
  function chart(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new Chart(document.getElementById(id), cfg); }
  function renderCharts() {
    const pmb = base('pm'), h = {};
    pmb.forEach(r => { const k = r.pm_label || '-'; h[k] = (h[k] || 0) + r.hours_left; });
    const top = Object.entries(h).sort((a, b) => b[1] - a[1]); const shown = top.slice(0, 12); const rest = top.slice(12).reduce((s, x) => s + x[1], 0); if (rest) shown.push(['All others', rest]);
    chart('ch-pm', {type: 'bar', data: {labels: shown.map(x => x[0]), datasets: [{data: shown.map(x => x[1]), backgroundColor: shown.map(x => x[0].toUpperCase() === fPM.toUpperCase() ? '#1f5eff' : PL.pmColor(x[0]) + 'aa'), borderWidth: 0}]},
      options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => PL.fmtNum(c.raw) + ' union hrs · ' + pmb.filter(r => (r.pm_label || '-') === c.label).length + ' tasks'}}}, scales: {y: {title: {display: true, text: 'Union hours'}, beginAtZero: true}, x: {ticks: {callback: function (v) { const l = this.getLabelForValue(v); return l.length > 12 ? l.slice(0, 11) + '…' : l; }}}},
        onClick: (e, els) => { if (!els.length) return; const k = shown[els[0].index][0]; if (k === 'All others') return; setPM(fPM.toUpperCase() === k.toUpperCase() ? 'ALL' : k); }}});
    const mb = base('month'), m = {};
    mb.forEach(r => { const k = monthKey(r); m[k] = (m[k] || 0) + r.hours_left; });
    const keys = Object.keys(m).sort((a, b) => a === 'No date' ? 1 : b === 'No date' ? -1 : a.localeCompare(b));
    const lbl = k => k === 'No date' ? k : new Date(+k.slice(0, 4), +k.slice(5, 7) - 1, 1).toLocaleDateString('en-US', {month: 'short', year: '2-digit'});
    chart('ch-month', {type: 'line', data: {labels: keys.map(lbl), datasets: [{data: keys.map(k => m[k]), borderColor: '#1f5eff', backgroundColor: '#1f5eff', pointRadius: keys.map(k => k === fMonth ? 7 : 5), pointHoverRadius: 8, tension: .25, fill: false}]},
      options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => PL.fmtNum(c.raw) + ' union hrs · ' + mb.filter(r => monthKey(r) === keys[c.dataIndex]).length + ' tasks'}}}, scales: {y: {beginAtZero: true, title: {display: true, text: 'Union hours'}}},
        onClick: (e, els) => { if (!els.length) return; const k = keys[els[0].index]; fMonth = fMonth === k ? 'ALL' : k; render(); }}});
    const pb = base('priority'), order = ['red', 'yellow', 'green', 'none'];
    chart('ch-pri', {type: 'bar', data: {labels: order.map(k => PL.PRIORITY_LABELS[k]), datasets: [{data: order.map(k => pb.filter(r => pri(r) === k).length), backgroundColor: order.map(k => PL.PRIORITY_COLORS[k] + (fPri === k ? '' : 'aa')), borderWidth: 0}]},
      options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}, tooltip: {callbacks: {label: c => c.raw + ' tasks · ' + PL.fmtNum(pb.filter(r => pri(r) === order[c.dataIndex]).reduce((s, r) => s + r.hours_left, 0)) + ' union hrs'}}}, scales: {y: {beginAtZero: true, ticks: {precision: 0}}},
        onClick: (e, els) => { if (!els.length) return; const k = order[els[0].index]; fPri = fPri === k ? 'ALL' : k; render(); }}});
    const sb = base('status'), stc = {};
    sb.forEach(r => stKey(r).forEach(s => (stc[s] = stc[s] || new Set()).add(projKey(r))));
    const sk = Object.keys(stc).sort((a, b) => (a === 'None' ? -1 : b === 'None' ? 1 : stc[b].size - stc[a].size)).slice(0, 12);
    chart('ch-st', {type: 'doughnut', data: {labels: sk, datasets: [{data: sk.map(k => stc[k].size), backgroundColor: sk.map(k => (V.colors[k] || ['#d9dee6'])[0]), borderColor: sk.map(k => k === fSt ? '#14202e' : '#fff'), borderWidth: sk.map(k => k === fSt ? 3 : 1)}]},
      options: {responsive: true, maintainAspectRatio: false, cutout: '58%', plugins: {legend: {position: 'right', labels: {boxWidth: 10, font: {size: 10}}, onClick: (e, item) => { const k = item.text; fSt = fSt === k ? 'ALL' : k; render(); }}, tooltip: {callbacks: {label: c => c.label + ': ' + c.raw + ' distinct projects · ' + sb.filter(r => stKey(r).includes(c.label)).length + ' tasks'}}},
        onClick: (e, els) => { if (!els.length) return; const k = sk[els[0].index]; fSt = fSt === k ? 'ALL' : k; render(); }}});
  }
  function renderTable(rows) {
    const getters = {name: r => r.name, pm: r => r.pm_label, start: r => r.start || '9999', end: r => r.end || '9999', hours_left: r => r.hours_left, percent: r => r.percent, status: r => r.phase_status};
    const sorted = PL.sortBy(rows, null, sort.dir, getters[sort.k] || getters.end);
    const th = (k, l, cls, tip) => '<th class="' + (cls || '') + (sort.k === k ? ' sorted' : '') + '" data-sort="' + k + '" title="' + esc(tip || l) + '" style="cursor:pointer">' + l + (sort.k === k ? (sort.dir > 0 ? ' ▴' : ' ▾') : '') + '</th>';
    $('#at-count').textContent = sorted.length + ' tasks';
    $('#at-table').innerHTML = '<thead><tr><th></th>' + th('name', 'Task name') + th('pm', 'PM', '', 'PM1 chip and PM2 chip (dashed) — click a chip to filter') + th('start', 'Start') + th('end', 'Critical stop') + th('hours_left', 'Union hrs', 'num', 'Union hours remaining — red above 99') + th('percent', 'Done', '', 'Percent complete') + th('status', 'Status', '', 'S1 = Phase Complete Status · S2 = Equipment Complete Status V2') + '<th></th></tr></thead><tbody>' +
      (sorted.length ? sorted.map(r => { const p = pri(r); const d = PL.daysUntil(r.end, today());
        return '<tr class="rowlink" data-id="' + r.id + '" title="Click for the project detail across divisions"><td><span class="pdot" style="background:' + PL.PRIORITY_COLORS[p] + '" title="' + PL.PRIORITY_LABELS[p] + '"></span></td>' +
          '<td><span class="tag" title="Row source">' + esc(r.source) + '</span> <b>' + esc(r.name) + '</b>' + (r.project_number ? ' <span class="muted">' + esc(r.project_number) + '</span>' : '') + '</td>' +
          '<td>' + (r.pm_label ? '<span class="pm-chip" data-pm="' + esc(r.pm_label) + '" style="border-color:' + PL.pmColor(r.pm_label) + '">' + esc(r.pm_label) + '</span>' : '<span class="muted">—</span>') + (r.pm2_label ? '<span class="pm-chip pm2" data-pm="' + esc(r.pm2_label) + '" title="PM2">' + esc(r.pm2_label) + '</span>' : (div === '040' ? '<span class="pm-chip pm2 muted">PM2 —</span>' : '')) + '</td>' +
          '<td>' + (PL.fmtDateShort(r.start) || '—') + '</td><td><span class="p-badge ' + p + '" title="' + PL.PRIORITY_LABELS[p] + (d != null ? ' · ' + d + ' days' : '') + '">' + (PL.fmtDateShort(r.end) || '—') + '</span></td>' +
          '<td class="num' + (r.hours_left > 99 ? ' hrs-hot' : '') + '">' + (r.hours_left ? PL.fmtNum(r.hours_left) : '—') + '</td><td><span class="done-bar"><span class="bar"><span style="width:' + Math.min(100, r.percent) + '%"></span></span>' + Math.round(r.percent) + '%</span></td>' +
          '<td>' + (r.phase_status || r.equipment_status ? (r.phase_status ? PL.pill(r.phase_status, V, ' <span class="src">S1</span>') : '') + ' ' + (r.equipment_status && (div === '040' || r.equipment_status !== r.phase_status) ? PL.pill(r.equipment_status, V, ' <span class="src">S2</span>') : '') : '<span class="st-pill blank">None / No phase selected</span>') + '</td>' +
          '<td style="white-space:nowrap"><a class="btn small" href="' + C.statusUrl + '?hl=' + encodeURIComponent(r.id) + '" title="Open this row on the status board" onclick="event.stopPropagation()">board ↗</a>' + (C.canWrite ? ' <button class="btn small" data-done="' + r.id + '" title="Owner the task complete on the status board (audited)">✓</button>' : '') + '</td></tr>'; }).join('') : '<tr><td colspan="9" class="empty">No tasks match the current filters</td></tr>') + '</tbody>';
  }
  function setPM(pm) { fPM = pm; PL.setPref('pm', pm); render(); }
  function detail(id) {
    const r = DS.rows.find(x => x.id === id); const key = projKey(r).toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/^0+/, '');
    const recs = ALL.filter(x => projKey(x).toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/^0+/, '') === key).sort((a, b) => a.division.localeCompare(b.division) || (a.end || '9999').localeCompare(b.end || '9999'));
    const pms = [...new Set(recs.flatMap(x => [x.pm_label, x.pm2_label]).filter(Boolean))], divs = [...new Set(recs.map(x => x.division))];
    const avg = Math.round(recs.reduce((s, x) => s + x.percent, 0) / recs.length), hrs = recs.reduce((s, x) => s + x.hours_left, 0);
    const start = recs.map(x => x.start).filter(Boolean).sort()[0], stop = recs.map(x => x.end).filter(Boolean).sort().slice(-1)[0];
    $('#at-mtitle').textContent = 'Project ' + (r.project_number || '') + ' – ' + r.name.replace(/^\d{6}\s*/, '');
    $('#at-msub').textContent = recs.length + ' active task record' + (recs.length > 1 ? 's' : '') + ' · ' + divs.join(' / ');
    const en = r.enrich;
    $('#at-mbody').innerHTML = '<div class="grid kpi" style="margin-bottom:10px">' +
      '<div class="kpi card tight"><div class="label">Average complete</div><div class="value">' + avg + '%</div></div><div class="kpi card tight"><div class="label">Union hours remaining</div><div class="value">' + PL.fmtNum(hrs) + '</div></div>' +
      '<div class="kpi card tight"><div class="label">Start</div><div class="value sm">' + (PL.fmtDate(start) || '—') + '</div><div class="sub">earliest</div></div><div class="kpi card tight"><div class="label">Critical stop</div><div class="value sm">' + (PL.fmtDate(stop) || '—') + '</div><div class="sub">latest</div></div>' +
      '<div class="kpi card tight"><div class="label">Project manager</div><div class="value sm">' + esc(pms.join(' / ') || '—') + '</div><div class="sub">' + (recs.some(x => x.pm2_label) ? 'includes PM2' : '') + '</div></div><div class="kpi card tight"><div class="label">Division</div><div class="value sm">' + divs.join(' / ') + '</div></div></div>' +
      (en ? '<div class="note" style="margin-bottom:10px">SL: contract ' + PL.money(en.contract_value) + ' · billed ' + PL.money(en.billed) + ' · PTT ' + PL.fmtNum(en.ptt_hours) + ' / ' + PL.fmtNum(en.budget_hours) + ' h · PM remaining ' + PL.fmtNum(en.remaining_hours) + ' h · last entry ' + (en.last_ptt ? PL.fmtDate(en.last_ptt) : '—') + ' · <a href="' + en.url + '">project page</a></div>' : '') +
      '<div class="row" style="margin-bottom:10px"><a class="btn primary" href="' + C.statusUrl + '?hl=' + encodeURIComponent(r.project_number || r.id) + '">Open in status board ↗</a></div>' +
      '<div class="table-wrap"><table class="data dense"><thead><tr><th>Division</th><th>Project / Task</th><th>PM1</th><th>PM2</th><th>Start</th><th>Critical stop</th><th class="num">Hours</th><th>Complete</th><th>Status</th><th>Status V2</th></tr></thead><tbody>' +
      recs.map(x => '<tr><td><span class="tag">' + x.division + '</span></td><td>' + esc(x.name) + '</td><td>' + esc(x.pm_label || '—') + '</td><td>' + esc(x.pm2_label || '—') + '</td><td>' + (PL.fmtDateShort(x.start) || '—') + '</td><td><span class="p-badge ' + pri(x) + '">' + (PL.fmtDateShort(x.end) || '—') + '</span></td><td class="num">' + PL.fmtNum(x.hours_left) + '</td><td>' + Math.round(x.percent) + '%</td><td>' + PL.pill(x.phase_status, V) + '</td><td>' + PL.pill(x.equipment_status, V) + '</td></tr>').join('') + '</tbody></table></div>';
    $('#at-modal').style.display = 'flex';
  }
  async function markDone(id) { const r = DS.rows.find(x => x.id === id); if (!(await PL.confirm('Owner "' + r.name + '" complete on the status board?', 'Owner complete'))) return; const res = await PL.post(C.editUrl, {op: 'complete', id, version: r.version, completed: true}); if (res.ok) { r.completed = true; ALL.splice(ALL.indexOf(r), 1); PL.toast('Marked complete'); render(); } else PL.toast(res.error, true); }
  // events
  $('#at-div').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; div = b.dataset.d; PL.setPref('div', div); fPri = 'ALL'; fSt = 'ALL'; fMonth = 'ALL'; render(); });
  $('#at-pms').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setPM(b.dataset.pm); });
  $('#at-pri').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { fPri = b.dataset.pri; render(); } });
  $('#at-st').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { fSt = b.dataset.st; render(); } });
  $('#at-q').addEventListener('input', PL.debounce(e => { q = e.target.value.trim().toLowerCase(); render(); }, 150));
  $('#at-reset').onclick = () => { fPM = 'ALL'; PL.setPref('pm', 'ALL'); fPri = 'ALL'; fSt = 'ALL'; fMonth = 'ALL'; fStart = false; q = ''; $('#at-q').value = ''; render(); };
  $('#at-focus').addEventListener('click', e => { if (e.target.id === 'at-clearpm') setPM('ALL'); });
  $('#at-kpi').addEventListener('click', e => { const t = e.target.closest('[data-k]'); if (!t) return; const k = t.dataset.k; if (k === 'red' || k === 'none') fPri = fPri === k ? 'ALL' : k; else if (k === 'start') fStart = !fStart; else if (k === 'urgent') fPri = fPri === 'red' ? 'yellow' : 'red'; else return; render(); });
  $('#at-table').addEventListener('click', e => { const chip = e.target.closest('[data-pm]'); if (chip) { setPM(fPM.toUpperCase() === chip.dataset.pm.toUpperCase() ? 'ALL' : chip.dataset.pm); return; }
    const dn = e.target.closest('button[data-done]'); if (dn) { markDone(+dn.dataset.done); return; }
    const th = e.target.closest('th[data-sort]'); if (th) { sort = sort.k === th.dataset.sort ? {k: sort.k, dir: -sort.dir} : {k: th.dataset.sort, dir: 1}; PL.setPref('sort', sort); renderTable(base()); return; }
    const tr = e.target.closest('tr[data-id]'); if (tr && !e.target.closest('a, button')) detail(+tr.dataset.id); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#at-modal').style.display = 'none'; });
  setInterval(render, 60000);   // the clock moves: priorities, KPIs and charts follow it
  render();
})();
