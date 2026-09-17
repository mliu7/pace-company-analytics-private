/* Punch lists (spec §6.4, PU-01…PU-08): PM overview → PM detail → project drawer; All projects grid; Overdue analysis.
   Inline edits post per field with a version check; derived status uses the live clock. */
(function () {
  const DS = JSON.parse(document.getElementById('pu-data').textContent), C = window.PU_CONF, esc = PL.esc, $ = s => document.querySelector(s);
  const projects = DS.projects, byId = new Map(projects.map(p => [p.id, p]));
  const today = () => PL.todayISO();
  const stats = p => PL.punchStats(p.items, today());
  const pmName = p => p.pm || 'Unassigned';
  let view = C.init.view || PL.pref('view', 'pms'), pmDetail = C.init.pm || null, expanded = new Set(PL.pref('expanded', [])), fPM = new Set(), fStatus = C.init.status || 'all', sortSel = PL.pref('sort', 'overdue'), q = '', drawerId = null, overdueSort = {k: 'days', dir: -1}, overdueFilter = null;
  const roster = () => { const names = DS.pms.map(p => p.name); projects.forEach(p => { if (!names.includes(pmName(p))) names.push(pmName(p)); }); if (!names.includes('Unassigned')) names.push('Unassigned'); return names.filter(n => n !== 'Unassigned').concat(['Unassigned']); };
  function msg(t, err) { const m = $('#pu-msg'); m.textContent = t; m.className = 'pl-status ' + (err ? 'err' : 'ok'); setTimeout(() => { if (m.textContent === t) m.textContent = ''; }, 5000); }
  // ---- stats strip (always the whole division)
  function renderStats() {
    const g = {projects: projects.length, total: 0, open: 0, overdue: 0, critical: 0, completed: 0};
    projects.forEach(p => { const s = stats(p); g.total += s.total; g.open += s.open; g.overdue += s.overdue; g.critical += s.critical; g.completed += s.completed; });
    const t = (l, v, cls, st, tip) => '<div class="kpi card tight click' + (fStatus === st && view === 'projects' ? ' on' : '') + ' ' + cls + '" data-st="' + st + '" title="' + esc(tip) + '"><div class="label">' + l + '</div><div class="value">' + v + '</div></div>';
    $('#pu-stats').innerHTML = t('Projects', g.projects, '', 'all', 'Projects in this division (click: All projects)') + t('Total items', g.total, '', 'all', 'Every punch item') + t('Open items', g.open, '', 'open', 'Items without a Date Completed') +
      t('Overdue', g.overdue, 'red', 'overdue', 'Open items due before today') + t('Critical open', g.critical, 'amber', 'critical', 'Open items at criticality 4–5') + t('Completed', g.completed, 'green', 'completed', 'Items with a Date Completed');
    document.querySelectorAll('#pu-nav button').forEach(b => b.classList.toggle('active', b.dataset.view === view || (view === 'pmdetail' && b.dataset.view === 'pms')));
  }
  const chips = p => p.src ? '<span class="chip" title="Status board: Phase status">' + esc(p.src.status || '—') + '</span>' + (p.src.status2 ? '<span class="chip" title="Equipment status V2">' + esc(p.src.status2) + '</span>' : '') + (p.src.finish ? '<span class="chip" title="Critical stop">Stop ' + PL.fmtDateShort(p.src.finish) + '</span>' : '') + (p.src.hours ? '<span class="chip" title="Union hours remaining on the status board">' + PL.fmtNum(p.src.hours) + ' hrs</span>' : '') : '';
  const pmSelect = (p, cls) => '<select data-pm-for="' + p.id + '" class="' + (pmName(p) === 'Unassigned' ? 'warn' : '') + (cls || '') + '"' + (C.canWrite ? '' : ' disabled') + ' title="Assign the PM (roster from core_employee)">' + roster().map(n => '<option' + (n === pmName(p) ? ' selected' : '') + '>' + (n === 'Unassigned' ? 'Assign PM…' : esc(n)) + '</option>').join('') + '</select>';
  function section(p, onlyOverdue) {
    const s = stats(p), open = expanded.has(p.id);
    return '<div class="pu-proj" data-pid="' + p.id + '"><div class="ph"><span class="chev" data-peek="' + p.id + '" title="Peek at the items in place">' + (open ? '▾' : '▸') + '</span><span class="code">' + esc(p.code) + '</span><span class="title" title="' + esc(p.title) + '">' + esc(p.title) + '</span>' + chips(p) +
      '<span class="counts">' + (onlyOverdue ? 'Overdue <b class="red">' + s.overdue + '</b> · Open ' + s.open + ' · Critical <b class="amber">' + s.critical + '</b>' : 'Open <b>' + s.open + '</b> · Overdue <b class="red">' + s.overdue + '</b> · Critical <b class="amber">' + s.critical + '</b> · ' + s.completed + '/' + s.total + ' done') + '</span>' +
      (onlyOverdue ? '' : pmSelect(p)) + (C.canWrite && !onlyOverdue ? '<button class="pl-close" data-delproj="' + p.id + '" title="Delete the project and all its items">✕</button>' : '') + '</div>' +
      (open ? '<div class="peek">' + itemTable(p, onlyOverdue) + '</div>' : '') + '</div>';
  }
  function itemTable(p, onlyOverdue) {
    const items = onlyOverdue ? p.items.filter(it => PL.punchStatus(it, today()) === 'overdue') : p.items;
    const ro = C.canWrite ? '' : ' disabled';
    const bics = DS.bics.slice(); items.forEach(it => { if (it.bic && !bics.includes(it.bic)) bics.push(it.bic); });
    const critBtn = it => { const c = it.critical; return '<button class="crit' + (c ? '' : ' blank') + '" data-crit="' + it.id + '" style="' + (c ? 'background:' + DS.critical[c].color : '') + '" title="' + (c ? c + ' ' + DS.critical[c].label : 'No criticality') + '"' + ro + '>' + (c || '—') + '</button>'; };
    return '<div class="pl-grid-wrap" style="max-height:60vh"><table class="pl-grid punch" style="min-width:1320px"><colgroup><col style="width:40px"><col style="width:120px"><col style="width:370px"><col style="width:170px"><col style="width:80px"><col style="width:120px"><col style="width:130px"><col style="width:120px"><col style="width:130px"><col style="width:130px"><col style="width:36px"></colgroup>' +
      '<thead><tr><th title="The spreadsheet tick — visual only, never changes the counts">Active</th><th>Date entered</th><th>Description of item</th><th title="Ball in court">BIC</th><th title="1 Lowest … 5 Highest; 4–5 count as critical">Critical</th><th>Due by</th><th title="The only signal that an item is finished">Date completed</th><th>Assigned</th><th>Engineer signoff</th><th>Verified (initials/date)</th><th></th></tr></thead><tbody>' +
      (items.length ? items.map(it => { const st = PL.punchStatus(it, today());
        return '<tr data-iid="' + it.id + '" class="' + (st === 'completed' || it.active ? 'done ' : '') + (st === 'overdue' ? 'overdue' : '') + '"><td style="text-align:center"><input type="checkbox" data-f="active" data-iid="' + it.id + '"' + (it.active ? ' checked' : '') + ro + ' style="width:auto"></td>' +
          '<td><input type="date" data-f="date_entered" data-iid="' + it.id + '" value="' + esc(it.date_entered || '') + '"' + ro + '></td><td><textarea data-f="description" data-iid="' + it.id + '" rows="1"' + ro + '>' + esc(it.description) + '</textarea></td>' +
          '<td><select data-f="bic" data-iid="' + it.id + '"' + ro + '><option value="">—</option>' + bics.map(b => '<option' + (b === it.bic ? ' selected' : '') + '>' + esc(b) + '</option>').join('') + '</select></td><td style="text-align:center">' + critBtn(it) + '</td>' +
          '<td><input type="date" class="' + (st === 'overdue' ? 'overdue' : '') + '" data-f="due_by" data-iid="' + it.id + '" value="' + esc(it.due_by || '') + '"' + ro + '></td><td><input type="date" data-f="date_completed" data-iid="' + it.id + '" value="' + esc(it.date_completed || '') + '"' + ro + '></td>' +
          '<td><input type="text" data-f="assigned" data-iid="' + it.id + '" value="' + esc(it.assigned) + '"' + ro + '></td><td><input type="text" data-f="engineer_signoff" data-iid="' + it.id + '" value="' + esc(it.engineer_signoff) + '"' + ro + '></td><td><input type="text" data-f="verified" data-iid="' + it.id + '" value="' + esc(it.verified) + '"' + ro + '></td>' +
          '<td>' + (C.canWrite ? '<button class="pl-close" data-delitem="' + it.id + '" title="Delete this punch item">✕</button>' : '') + '</td></tr>'; }).join('') : '<tr><td colspan="11" class="empty">' + (onlyOverdue ? 'No overdue items' : 'No punch items yet') + '</td></tr>') +
      '</tbody></table></div>' + (C.canWrite && !onlyOverdue ? '<div style="margin-top:6px"><button class="btn small" data-additem="' + p.id + '">+ Add punch</button></div>' : '');
  }
  function autosize(root) { (root || document).querySelectorAll('table.pl-grid.punch textarea').forEach(t => { t.style.height = 'auto'; t.style.height = Math.max(30, t.scrollHeight + 2) + 'px'; }); }
  // ---- views
  function renderView() {
    const v = $('#pu-view');
    if (view === 'pms') {
      const names = roster(); const present = names.filter(n => projects.some(p => pmName(p) === n) || n === 'Unassigned' || DS.pms.some(x => x.name === n && x.n_open));
      v.innerHTML = '<div class="pu-cards">' + present.map(n => { const ps = projects.filter(p => pmName(p) === n); const g = {open: 0, overdue: 0, critical: 0, completed: 0}; ps.forEach(p => { const s = stats(p); g.open += s.open; g.overdue += s.overdue; g.critical += s.critical; g.completed += s.completed; });
        return '<div class="pu-card' + (n === 'Unassigned' ? ' unassigned' : '') + '" data-pm="' + esc(n) + '" style="border-top-color:' + (n === 'Unassigned' ? '#b7791f' : PL.pmColor(n)) + '" title="Open the PM\'s projects"><div class="name">' + (n === 'Unassigned' ? '⚠ ' : '') + esc(n) + '</div><div class="sub">' + ps.length + ' project' + (ps.length === 1 ? '' : 's') + (n === 'Unassigned' ? ' · needs a PM assigned' : (ps.length ? '' : ' · ready for assignments')) + '</div><div class="m"><span>Open<b>' + g.open + '</b></span><span>Overdue<b class="red">' + g.overdue + '</b></span><span>Critical<b class="amber">' + g.critical + '</b></span><span>Done<b class="green">' + g.completed + '</b></span></div></div>'; }).join('') + '</div>';
    } else if (view === 'pmdetail') {
      const ps = projects.filter(p => pmName(p) === pmDetail).sort((a, b) => { const sa = stats(a), sb = stats(b); return (sb.overdue - sa.overdue) || (sb.open - sa.open) || a.code.localeCompare(b.code); });
      const g = {open: 0, overdue: 0, critical: 0, completed: 0}; ps.forEach(p => { const s = stats(p); g.open += s.open; g.overdue += s.overdue; g.critical += s.critical; g.completed += s.completed; });
      v.innerHTML = '<div class="row" style="margin-bottom:10px"><button class="btn small" id="pu-back">← All PMs</button><span class="pill" style="background:' + PL.pmColor(pmDetail) + ';color:#fff;border-color:transparent">' + esc(pmDetail.slice(0, 1)) + '</span><h2 style="margin:0">' + esc(pmDetail) + '</h2><span class="muted">' + ps.length + ' projects · ' + g.open + ' open · ' + g.overdue + ' overdue · ' + g.critical + ' critical · ' + g.completed + ' completed</span></div><div class="note" style="margin-bottom:8px">Click a project to open its punches; the chevron peeks in place.</div>' + (ps.length ? ps.map(p => section(p, false)).join('') : '<div class="empty">No projects for this PM.</div>');
    } else if (view === 'projects') {
      const present = [...new Set(projects.map(pmName))].sort();
      let list = projects.filter(p => !fPM.size || fPM.has(pmName(p))).filter(p => { if (fStatus === 'all') return true; const s = stats(p); return fStatus === 'open' ? s.open : fStatus === 'overdue' ? s.overdue : fStatus === 'critical' ? s.critical : fStatus === 'completed' ? s.completed : true; })
        .filter(p => !q || (p.code + ' ' + p.title + ' ' + p.items.map(i => i.description).join(' ')).toLowerCase().includes(q));
      list.sort((a, b) => { const sa = stats(a), sb = stats(b); return sortSel === 'open' ? (sb.open - sa.open) || a.code.localeCompare(b.code) : sortSel === 'alpha' ? a.title.localeCompare(b.title) : sortSel === 'pm' ? pmName(a).localeCompare(pmName(b)) || a.title.localeCompare(b.title) : (sb.overdue - sa.overdue) || (sb.open - sa.open) || a.code.localeCompare(b.code); });
      v.innerHTML = '<div class="pl-toolbar"><div class="seg">' + present.map(n => '<button class="pmchip ' + (fPM.has(n) ? 'active' : '') + '" data-fpm="' + esc(n) + '" style="border-left-color:' + PL.pmColor(n) + '">' + esc(n) + ' <span class="n">' + projects.filter(p => pmName(p) === n).length + '</span></button>').join('') + '</div>' +
        '<div class="seg">' + [['all', 'All'], ['open', 'Open'], ['overdue', 'Overdue'], ['critical', 'Critical'], ['completed', 'Completed']].map(([k, l]) => '<button data-fst="' + k + '" class="' + (fStatus === k ? 'active' : '') + '" title="Projects with at least one item in this state">' + l + '</button>').join('') + '</div>' +
        '<select id="pu-sort" style="font:inherit;padding:5px 8px;border:1px solid var(--line-strong);border-radius:8px"><option value="overdue"' + (sortSel === 'overdue' ? ' selected' : '') + '>Most overdue</option><option value="open"' + (sortSel === 'open' ? ' selected' : '') + '>Most open items</option><option value="alpha"' + (sortSel === 'alpha' ? ' selected' : '') + '>Alphabetical</option><option value="pm"' + (sortSel === 'pm' ? ' selected' : '') + '>PM</option></select><span class="muted">' + list.length + ' of ' + projects.length + ' projects</span></div>' +
        '<div class="pu-grid">' + (list.length ? list.map(p => { const s = stats(p); return '<div class="pu-pcard" data-open="' + p.id + '"><div class="code">' + esc(p.code) + (p.project_url ? ' <a href="' + p.project_url + '" title="SL project page" onclick="event.stopPropagation()">↗</a>' : '') + '</div><div class="title" title="' + esc(p.title) + '">' + esc(p.title) + '</div><div class="m"><span class="pill" style="border-color:' + PL.pmColor(pmName(p)) + '">' + esc(pmName(p)) + '</span><span>Open <b>' + s.open + '</b></span><span>Overdue <b class="red">' + s.overdue + '</b></span><span>Critical <b class="amber">' + s.critical + '</b></span><span>Done <b>' + s.completed + '/' + s.total + '</b></span></div><div class="bar' + (s.pct === 100 ? ' good' : '') + '"><span style="width:' + s.pct + '%"></span></div></div>'; }).join('') : '<div class="empty">No projects match the current filters</div>') + '</div>';
    } else if (view === 'overdue') {
      let rows = [], affected = new Set(), hi = 0, c5 = 0, most = 0;
      projects.forEach(p => p.items.forEach(it => { if (it.date_completed) return; if (PL.critOpen(it)) hi++; if (+it.critical === 5) c5++; const d = PL.daysUntil(it.due_by, today()); if (d != null && d < 0) { rows.push({p, it, days: -d, bic: it.bic || 'Unspecified', pm: pmName(p)}); affected.add(p.id); most = Math.max(most, -d); } }));
      const byBic = {}, byPm = {}; rows.forEach(r => { byBic[r.bic] = (byBic[r.bic] || 0) + 1; byPm[r.pm] = (byPm[r.pm] || 0) + 1; });
      const bars = (obj, cls, key) => { const es = Object.entries(obj).sort((a, b) => b[1] - a[1]); const mx = Math.max(1, ...es.map(e => e[1])); return es.length ? '<div class="bars">' + es.map(([k, v], i) => '<div class="brow click" data-of="' + key + '" data-ov="' + esc(k) + '" title="Click to show only these overdue items"><span class="lab">' + esc(k) + '</span><span class="bar"><span style="width:' + Math.round(100 * v / mx) + '%;background:' + (key === 'pm' ? PL.pmColor(k) : PCA.colors[i % PCA.colors.length]) + '"></span></span><span class="v">' + v + '</span></div>').join('') + '</div>' : '<div class="empty">No overdue items 🎉</div>'; };
      let shown = overdueFilter ? rows.filter(r => r[overdueFilter.k] === overdueFilter.v) : rows;
      shown = PL.sortBy(shown, null, overdueSort.dir, r => overdueSort.k === 'days' ? r.days : overdueSort.k === 'project' ? r.p.code : overdueSort.k === 'crit' ? (r.it.critical || 0) : overdueSort.k === 'due' ? r.it.due_by : overdueSort.k === 'bic' ? r.bic : r.pm);
      const th = (k, l, cls) => '<th class="' + (cls || '') + (overdueSort.k === k ? ' sorted' : '') + '" data-osort="' + k + '" style="cursor:pointer">' + l + (overdueSort.k === k ? (overdueSort.dir > 0 ? ' ▴' : ' ▾') : '') + '</th>';
      const projSections = projects.filter(p => affected.has(p.id)).sort((a, b) => (stats(b).overdue - stats(a).overdue) || a.code.localeCompare(b.code));
      v.innerHTML = '<div class="note" style="margin-bottom:10px">Counts here are based on Date Completed, not the Active checkbox — an item ticked Active without a completion date is still open, overdue and critical. Today is the live clock; items due today are not overdue.</div>' +
        '<div class="grid kpi" style="margin-bottom:12px"><div class="kpi card tight red"><div class="label">Total overdue items</div><div class="value">' + rows.length + '</div></div><div class="kpi card tight"><div class="label">Projects affected</div><div class="value">' + affected.size + '</div></div><div class="kpi card tight red"><div class="label">Most days overdue</div><div class="value">' + most + '</div></div><div class="kpi card tight amber"><div class="label">Critical level 5 (open)</div><div class="value">' + c5 + '</div></div><div class="kpi card tight amber"><div class="label">High priority 4–5 (open)</div><div class="value">' + hi + '</div></div></div>' +
        '<div class="charts2"><div class="card"><h3>Overdue items by department (BIC)</h3>' + bars(byBic, '', 'bic') + '</div><div class="card"><h3>Overdue items by PM</h3>' + bars(byPm, '', 'pm') + '</div></div>' +
        '<div class="card tight" style="margin-bottom:12px"><h2>All overdue items <small>' + shown.length + (overdueFilter ? ' · ' + esc(overdueFilter.v) + ' <button class="btn small" id="pu-ovclear">clear</button>' : '') + '</small></h2><div class="table-wrap tall"><table class="data dense"><thead><tr>' + th('project', 'Project') + '<th>Description</th>' + th('bic', 'BIC') + th('crit', 'Crit') + th('due', 'Due by') + th('days', 'Days overdue', 'num') + th('pm', 'PM') + '<th>Assigned</th></tr></thead><tbody>' +
        (shown.length ? shown.map(r => '<tr><td><a href="#" data-open="' + r.p.id + '" class="mono" title="Open the project drawer">' + esc(r.p.code) + '</a></td><td>' + esc((r.it.description || '').slice(0, 140)) + '</td><td>' + esc(r.bic) + '</td><td>' + (r.it.critical ? '<span class="crit" style="background:' + DS.critical[r.it.critical].color + '">' + r.it.critical + '</span>' : '—') + '</td><td>' + PL.fmtDateShort(r.it.due_by) + '</td><td class="num" style="color:var(--bad);font-family:var(--mono)">' + r.days + 'd</td><td>' + esc(r.pm) + '</td><td>' + esc(r.it.assigned) + '</td></tr>').join('') : '<tr><td colspan="8" class="empty">No overdue items 🎉</td></tr>') + '</tbody></table></div></div>' +
        '<div class="card tight"><h2>Projects with overdue items <small>' + projSections.length + '</small></h2>' + (projSections.length ? projSections.map(p => section(p, true)).join('') : '<div class="empty">None</div>') + '</div>';
    }
    autosize(v);
  }
  function render() { renderStats(); renderView(); }
  // ---- drawer
  function openDrawer(id) {
    const p = byId.get(id); if (!p) return; drawerId = id;
    $('#pu-drawer-head').innerHTML = '<span class="code mono" style="font-weight:600">' + esc(p.code) + '</span>' + (C.canWrite ? '<input id="pu-title" value="' + esc(p.title) + '" style="flex:1;font:inherit;font-weight:600;font-size:15px;border:1px solid transparent;border-radius:6px;padding:3px 6px" title="Edit the title; saves when you leave the field">' : '<h2>' + esc(p.title) + '</h2>') + pmSelect(p) + (p.project_url ? '<a class="btn small" href="' + p.project_url + '">SL project ↗</a>' : '') + (p.src ? '<a class="btn small" href="' + C.statusUrl + '?hl=' + p.src.row_id + '" title="The status board row">status ↗</a>' : '') + (C.canWrite ? '<button class="btn small" data-delproj="' + p.id + '" style="color:var(--bad)">Delete project</button>' : '') + '<button class="pl-close" id="pu-drawer-close">✕</button>';
    $('#pu-drawer-body').innerHTML = (p.src ? '<div class="note" style="margin-bottom:8px">' + chips(p) + '</div>' : '') + itemTable(p, false);
    $('#pu-drawer-foot').innerHTML = '<span class="muted">' + stats(p).completed + '/' + stats(p).total + ' done · edits save as you go</span>';
    $('#pu-drawer').classList.add('open'); $('#pu-drawer-bg').classList.add('open'); document.body.style.overflow = 'hidden'; autosize($('#pu-drawer'));
    const t = $('#pu-title'); if (t) t.addEventListener('change', async () => { const v = t.value.trim(); if (!v) { t.value = p.title; return; } const res = await PL.post(C.editUrl, {op: 'project_update', id: p.id, fields: {title: v}}); if (res.ok) { Object.assign(p, res.project, {items: p.items, src: p.src}); msg('Title saved'); } else PL.toast(res.error, true); });
  }
  function closeDrawer() { $('#pu-drawer').classList.remove('open'); $('#pu-drawer-bg').classList.remove('open'); document.body.style.overflow = ''; drawerId = null; render(); }
  // ---- edits
  function findItem(id) { for (const p of projects) { const it = p.items.find(i => i.id === id); if (it) return [p, it]; } return [null, null]; }
  async function saveItem(id, field, value, el) {
    const [p, it] = findItem(id); if (!it) return;
    const res = await PL.post(C.editUrl, {op: 'item_update', id, version: it.version, field, value});
    if (res.ok) { Object.assign(it, res.item); msg('Saved'); if (['due_by', 'date_completed', 'active', 'critical'].includes(field)) refreshAfter(p, it, el); }
    else { PL.toast(res.error, true); if (res.item) Object.assign(it, res.item); }
  }
  function refreshAfter(p, it, el) {
    // recolour the row and refresh every count without losing focus: the row class in place, the strip and headers re-rendered
    document.querySelectorAll('tr[data-iid="' + it.id + '"]').forEach(tr => { const st = PL.punchStatus(it, today()); tr.className = (st === 'completed' || it.active ? 'done ' : '') + (st === 'overdue' ? 'overdue' : ''); const d = tr.querySelector('[data-f=due_by]'); if (d) d.classList.toggle('overdue', st === 'overdue'); });
    renderStats();
    document.querySelectorAll('.pu-proj[data-pid="' + p.id + '"] .ph .counts').forEach(c => { const s = stats(p); c.innerHTML = 'Open <b>' + s.open + '</b> · Overdue <b class="red">' + s.overdue + '</b> · Critical <b class="amber">' + s.critical + '</b> · ' + s.completed + '/' + s.total + ' done'; });
    if (drawerId === p.id) $('#pu-drawer-foot').innerHTML = '<span class="muted">' + stats(p).completed + '/' + stats(p).total + ' done · edits save as you go</span>';
  }
  const deb = {};
  document.addEventListener('input', e => { const el = e.target; if (!el.dataset.f || !el.dataset.iid || el.type === 'date' || el.type === 'checkbox' || el.tagName === 'SELECT') return; if (el.tagName === 'TEXTAREA') { el.style.height = 'auto'; el.style.height = Math.max(30, el.scrollHeight + 2) + 'px'; } const k = el.dataset.iid + el.dataset.f; (deb[k] = deb[k] || PL.debounce((id, f, v, x) => saveItem(id, f, v, x), 650))(+el.dataset.iid, el.dataset.f, el.value, el); });
  document.addEventListener('change', async e => { const el = e.target;
    if (el.dataset.f && el.dataset.iid) { saveItem(+el.dataset.iid, el.dataset.f, el.type === 'checkbox' ? el.checked : el.value, el); return; }
    if (el.dataset.pmFor) { const p = byId.get(+el.dataset.pmFor); const res = await PL.post(C.editUrl, {op: 'project_update', id: p.id, fields: {pm: el.value === 'Assign PM…' ? 'Unassigned' : el.value}}); if (res.ok) { Object.assign(p, res.project, {items: p.items, src: p.src}); PL.toast(p.code + ' assigned to ' + pmName(p)); render(); if (drawerId) openDrawer(drawerId); } else PL.toast(res.error, true); return; }
    if (el.id === 'pu-sort') { sortSel = el.value; PL.setPref('sort', sortSel); renderView(); }
  });
  let pop = null;
  function critPopover(btn, id) {
    if (pop) pop.remove(); pop = document.createElement('div'); pop.className = 'crit-pop';
    pop.innerHTML = '<button data-c="">— None</button>' + [1, 2, 3, 4, 5].map(c => '<button data-c="' + c + '"><i style="background:' + DS.critical[c].color + '"></i>' + c + ' ' + DS.critical[c].label + '</button>').join('');
    document.body.appendChild(pop); const r = btn.getBoundingClientRect(); pop.style.left = Math.min(r.left, window.innerWidth - 180) + 'px'; pop.style.top = (r.bottom + window.scrollY + 4) + 'px';
    pop.addEventListener('click', e => { const b = e.target.closest('button[data-c]'); if (!b) return; saveItem(id, 'critical', b.dataset.c, btn).then(() => { const [, it] = findItem(id); btn.textContent = it.critical || '—'; btn.className = 'crit' + (it.critical ? '' : ' blank'); btn.style.background = it.critical ? DS.critical[it.critical].color : ''; PL.toast('Critical level set to ' + (it.critical || 'none')); }); pop.remove(); pop = null; });
  }
  document.addEventListener('click', async e => {
    if (pop && !e.target.closest('.crit-pop, .crit')) { pop.remove(); pop = null; }
    const t = e.target;
    const crit = t.closest('button[data-crit]'); if (crit) { critPopover(crit, +crit.dataset.crit); return; }
    const peek = t.closest('[data-peek]'); if (peek) { const id = +peek.dataset.peek; expanded.has(id) ? expanded.delete(id) : expanded.add(id); PL.setPref('expanded', [...expanded]); renderView(); return; }
    const add = t.closest('[data-additem]'); if (add) { const p = byId.get(+add.dataset.additem); const res = await PL.post(C.editUrl, {op: 'item_add', project_id: p.id}); if (res.ok) { p.items.push(res.item); expanded.add(p.id); if (drawerId === p.id) openDrawer(p.id); else renderView(); PL.toast('Punch added'); } else PL.toast(res.error, true); return; }
    const di = t.closest('[data-delitem]'); if (di) { const [p, it] = findItem(+di.dataset.delitem); if (!(await PL.confirm('Delete this punch item?\n\n' + (it.description || '').slice(0, 107), 'Delete'))) return; const res = await PL.post(C.editUrl, {op: 'item_delete', id: it.id}); if (res.ok) { p.items.splice(p.items.indexOf(it), 1); if (drawerId === p.id) openDrawer(p.id); else render(); PL.toast('Deleted'); } else PL.toast(res.error, true); return; }
    const dp = t.closest('[data-delproj]'); if (dp) { const p = byId.get(+dp.dataset.delproj); if (!(await PL.confirm('Delete project ' + p.code + ' and all ' + p.items.length + ' items? This cannot be undone.', 'Delete project'))) return; const res = await PL.post(C.editUrl, {op: 'project_delete', id: p.id}); if (res.ok) { projects.splice(projects.indexOf(p), 1); byId.delete(p.id); if (drawerId === p.id) closeDrawer(); else render(); PL.toast('Project deleted'); } else PL.toast(res.error, true); return; }
    const op = t.closest('[data-open]'); if (op) { e.preventDefault(); openDrawer(+op.dataset.open); return; }
    const card = t.closest('.pu-card[data-pm]'); if (card) { view = 'pmdetail'; pmDetail = card.dataset.pm; render(); return; }
    if (t.id === 'pu-back') { view = 'pms'; PL.setPref('view', 'pms'); render(); return; }
    const ph = t.closest('.pu-proj .ph'); if (ph && !t.closest('select, button, .chev, a')) { openDrawer(+ph.parentElement.dataset.pid); return; }
    const nav = t.closest('#pu-nav button'); if (nav) { view = nav.dataset.view; PL.setPref('view', view); render(); return; }
    const st = t.closest('#pu-stats [data-st]'); if (st) { view = 'projects'; fStatus = st.dataset.st; PL.setPref('view', view); render(); return; }
    const fpm = t.closest('[data-fpm]'); if (fpm) { fPM.has(fpm.dataset.fpm) ? fPM.delete(fpm.dataset.fpm) : fPM.add(fpm.dataset.fpm); renderView(); return; }
    const fst = t.closest('[data-fst]'); if (fst) { fStatus = fst.dataset.fst; render(); return; }
    const of = t.closest('[data-of]'); if (of) { overdueFilter = (overdueFilter && overdueFilter.k === of.dataset.of && overdueFilter.v === of.dataset.ov) ? null : {k: of.dataset.of, v: of.dataset.ov}; renderView(); return; }
    if (t.id === 'pu-ovclear') { overdueFilter = null; renderView(); return; }
    const os = t.closest('th[data-osort]'); if (os) { overdueSort = overdueSort.k === os.dataset.osort ? {k: overdueSort.k, dir: -overdueSort.dir} : {k: os.dataset.osort, dir: os.dataset.osort === 'days' ? -1 : 1}; renderView(); return; }
    if (t.id === 'pu-drawer-close' || t.id === 'pu-drawer-bg') closeDrawer();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (pop) { pop.remove(); pop = null; } else if (drawerId) closeDrawer(); } });
  $('#pu-q').addEventListener('input', PL.debounce(e => { q = e.target.value.trim().toLowerCase(); if (q && view !== 'projects') { view = 'projects'; PL.setPref('view', view); } render(); }, 150));
  if (C.canWrite) {
    $('#pu-sync').onclick = async () => { const res = await PL.post(C.editUrl, {op: 'seed', div: DS.division}); if (res.ok) { PL.toast('Status board linked: ' + res.added + ' project' + (res.added === 1 ? '' : 's') + ' added'); if (res.added) location.reload(); } else PL.toast(res.error, true); };
    $('#pu-addproj').onclick = () => {
      const m = document.createElement('div'); m.className = 'fin-modal'; m.style.display = 'flex'; m.style.zIndex = 95;
      m.innerHTML = '<div class="fin-modal-box" style="max-width:480px"><div class="fin-modal-head"><h2>Add project · ' + DS.division + '</h2></div><form class="pl-form" id="pu-addform"><label class="req">Project code<input name="code" placeholder="26-0200 or 260200" required></label><label>Project title<input name="title" placeholder="defaults to the code"></label><label class="full">Project manager<select name="pm">' + roster().map(n => '<option' + (n === 'Unassigned' ? ' selected' : '') + '>' + esc(n) + '</option>').join('') + '</select></label><div class="err" id="pu-adderr"></div></form><div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn" data-x="0">Cancel</button><button class="btn primary" data-x="1">Add project</button></div></div>';
      document.body.appendChild(m);
      m.addEventListener('click', async e => { const b = e.target.closest('button[data-x]'); if (!b && e.target !== m) return; if (!b || b.dataset.x === '0') { m.remove(); return; }
        const f = m.querySelector('#pu-addform'); const res = await PL.post(C.editUrl, {op: 'project_add', div: DS.division, code: f.code.value, title: f.title.value, pm: f.pm.value});
        if (res.ok) { res.project.items = res.project.items || []; projects.push(res.project); byId.set(res.project.id, res.project); m.remove(); render(); openDrawer(res.project.id); PL.toast('Project added'); } else m.querySelector('#pu-adderr').textContent = res.error; });
      setTimeout(() => m.querySelector('[name=code]').focus(), 30);
    };
  }
  if (C.init.pm) { view = 'pmdetail'; pmDetail = C.init.pm; }
  render();
  if (C.openPk) openDrawer(C.openPk);
  setInterval(render, 60000);
})();
