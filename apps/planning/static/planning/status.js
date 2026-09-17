/* Status board grid (spec §6.1, PS-01…PS-10): client-side rendering of the full dataset embedded in the page, inline
   editing persisted row by row (optimistic version check), drawer form, column resize / auto-fit / zoom / height
   remembered with PCA.pref, sort on every column, Send to Scheduler, deep-link highlight (?hl=). */
(function () {
  const DS = JSON.parse(document.getElementById('st-data').textContent), C = window.ST_CONF, V = DS.vocab;
  const $ = s => document.querySelector(s), esc = PL.esc;
  const rowsById = new Map(DS.rows.map(r => [r.id, r]));
  const closedSet = new Set(DS.closed_in_sl || []);
  const pmNames = DS.pms.map(p => p.name);
  // ---- columns (order = the dashboard's 13, then PCA's additions; `sl` group = live enrichment, hidden by default)
  const COLS = [
    {k: 'idx', h: '#', w: 36, fz: true, min: 28},
    {k: 'done', h: '✓', w: 34, fz: true, min: 28, tip: 'Owner complete and move to Completed Projects (manual only, reversible)'},
    {k: 'task', h: 'Project / Task Description', w: 340, fz: true, last: true, min: 180, max: 900, sort: r => r.name, tip: 'Name, project number and the drawer (⋯). Hover for SL / PTT figures.'},
    {k: 'division', h: 'DIV', w: 72, min: 56, edit: 'text', sort: r => r.division},
    {k: 'phase_status', h: 'Phase Complete Status', w: 220, min: 120, max: 520, edit: 'status', sort: r => r.phase_status, tip: 'Status pill 1 — the same vocabulary as Equipment Complete Status V2; no transition is enforced'},
    {k: 'equipment_status', h: 'Equipment Complete Status V2', w: 220, min: 120, max: 520, edit: 'status', sort: r => r.equipment_status, tip: 'Status pill 2 (S2)'},
    {k: 'pm', h: 'PM', w: 130, min: 75, max: 380, edit: 'pm', sort: r => r.pm_label, tip: 'Project manager — pick a person from the roster or type a name'},
    {k: 'pm2', h: 'PM2', w: 110, min: 75, max: 380, edit: 'pm', sort: r => r.pm2_label, opt: true, tip: 'Second PM — counted by the Active tasks PM filter'},
    {k: 'engineer', h: 'Engineer', w: 120, min: 75, max: 380, edit: 'text', sort: r => r.engineer},
    {k: 'start', h: 'Start Date', w: 122, min: 88, max: 220, edit: 'date', sort: r => r.start},
    {k: 'end', h: 'End Date', w: 122, min: 88, max: 220, edit: 'date', sort: r => r.end, tip: 'Critical stop date — red when before today (live clock)'},
    {k: 'hours_left', h: 'Hours Left', w: 86, min: 70, edit: 'number', sort: r => r.hours_left, tip: 'Union hours remaining (the scheduler hand-off labels them union)'},
    {k: 'percent', h: '% Done', w: 76, min: 60, edit: 'number', sort: r => r.percent, tip: 'Percent complete — the dashboard carried it but never showed it'},
    {k: 'notes', h: 'Notes', w: 340, min: 120, edit: 'text', sort: r => r.notes, tip: 'Imported from the Planner Update Notes / Notes column and editable here; the drawer shows the full text'},
    {k: 'updated', h: 'Last Updated', w: 150, min: 115, max: 260, sort: r => r.updated_at, tip: 'Uses the Last Update Date from the imported Planner file; edits here replace it with the edit date, time and person'},
    {k: 'cv', h: 'SL contract value', w: 110, opt: true, grp: 'sl', num: true, sort: r => r.enrich && r.enrich.contract_value, tip: 'Contract value the app uses for the SL project (nightly refresh)'},
    {k: 'billed', h: 'SL billed', w: 100, opt: true, grp: 'sl', num: true, sort: r => r.enrich && r.enrich.billed, tip: 'Billed revenue on the SL project'},
    {k: 'ptt', h: 'PTT hrs / budget', w: 120, opt: true, grp: 'sl', num: true, sort: r => r.enrich && r.enrich.ptt_hours, tip: 'PTT field hours to date against the SL labor-hour budget'},
    {k: 'rem', h: 'PTT remaining', w: 100, opt: true, grp: 'sl', num: true, sort: r => r.enrich && r.enrich.remaining_hours, tip: "The PM's remaining-hours estimate in PTT (date of the estimate on hover)"},
    {k: 'lastptt', h: 'Last PTT entry', w: 100, opt: true, grp: 'sl', sort: r => r.enrich && r.enrich.last_ptt, tip: 'Most recent PTT job report on the project'},
    {k: 'estimator', h: 'Estimator', w: 120, opt: true, grp: 'sl', sort: r => r.enrich && r.enrich.estimator, tip: "The bid's estimator (SL project → bid link)"},
    {k: 'source', h: 'Source', w: 80, opt: true, sort: r => r.source, tip: 'manual / import / Planner'},
  ];
  const DEFAULT_HIDDEN = COLS.filter(c => c.opt).map(c => c.k);
  // ---- remembered UI state
  let view = PL.pref('view', 'active'), div = C.div || PL.pref('div', 'ALL'), q = '', statusFilter = C.status || '';
  let sort = PL.pref('sort', null), widths = PL.pref('widths', {}), hidden = PL.pref('hidden', DEFAULT_HIDDEN), zoom = PL.pref('zoom', 100), height = PL.pref('height', 'auto');
  let sel = null, visible = [];
  const today = () => PL.todayISO();
  const divisions = DS.divisions;
  const visCols = () => COLS.filter(c => !hidden.includes(c.k));
  // ---- data helpers
  const rowText = r => [r.name, r.project_number, r.division, r.phase_status, r.equipment_status, r.pm_label, r.pm2_label, r.engineer, r.notes, r.start, r.end].join(' ').toLowerCase();
  function viewRows() { return DS.rows.filter(r => (view === 'completed') === !!r.completed).filter(r => div === 'ALL' || r.division === div); }
  function visibleRows() {
    let rows = viewRows();
    if (statusFilter) rows = rows.filter(r => r.phase_status === statusFilter || r.equipment_status === statusFilter);
    if (q) rows = rows.filter(r => rowText(r).includes(q));
    if (sort && sort.k) { const c = COLS.find(x => x.k === sort.k); if (c && c.sort) rows = PL.sortBy(rows, null, sort.dir, c.sort); }
    else rows.sort((a, b) => (b.source === 'manual') - (a.source === 'manual') || (a.source === 'manual' ? b.id - a.id : 0) || a.division.localeCompare(b.division) || (a.task_num || 0) - (b.task_num || 0) || a.id - b.id);
    return rows;
  }
  const upd = r => r.updated_at ? PL.fmtDT(r.updated_at) + (r.updated_by ? ' · ' + r.updated_by : '') : (r.last_update_date ? PL.fmtDate(r.last_update_date) : 'Not updated yet');
  // ---- rendering
  function statusSelect(r, k) {
    const v = r[k] || '', known = V.keys.includes(v);
    const opts = V.keys.map(s => '<option value="' + esc(s) + '"' + (s === v ? ' selected' : '') + '>' + (s || '— none —') + '</option>').join('') + (known ? '' : '<option value="' + esc(v) + '" selected>' + esc(v) + '</option>');
    const from = r.planner && r.planner[k === 'phase_status' ? 'phase' : 'equipment'];
    return '<select class="st" data-f="' + k + '" data-id="' + r.id + '" style="' + PL.pillStyle(v, V) + '"' + (C.canWrite ? '' : ' disabled') + '>' + opts + '</select>' + (from ? '<span class="src" title="This pill follows the Planner label; a manual edit wins until the Planner value changes again">from Planner</span>' : '');
  }
  function cell(r, c, i) {
    const ro = C.canWrite ? '' : ' disabled';
    switch (c.k) {
      case 'idx': return '<span class="idx">' + (i + 1) + '</span>';
      case 'done': return '<button class="cbtn' + (r.completed ? ' on' : '') + '" data-act="complete" data-id="' + r.id + '" title="' + (r.completed ? 'Return to Active Projects' : 'Owner complete and move to Completed Projects') + '"' + ro + '>✓</button>';
      case 'task': {
        const tags = (r.sent_to_scheduler_at ? ' <span class="chip" title="Sent to the Resource Scheduler ' + esc(PL.fmtDT(r.sent_to_scheduler_at)) + '">sent</span>' : '') + (closedSet.has(r.project_id) ? ' <span class="chip neg" title="The SL project is closed — retire or complete this row">closed in SL</span>' : '') + (r.source === 'manual' ? '' : '');
        return '<div style="display:flex;align-items:center;gap:4px"><span class="task" style="flex:1" data-hover="' + r.id + '"><span>' + esc(r.name) + '</span>' + tags + '<small>' + (r.project_number ? esc(r.project_number) : '—') + (r.enrich && r.enrich.customer ? ' · ' + esc(r.enrich.customer) : '') + '</small></span><button class="more" data-act="drawer" data-id="' + r.id + '" title="Actions and the full edit form">⋯</button></div>';
      }
      case 'division': return '<input type="text" data-f="division" data-id="' + r.id + '" value="' + esc(r.division) + '"' + ro + '>';
      case 'phase_status': case 'equipment_status': return statusSelect(r, c.k);
      case 'pm': case 'pm2': return '<input type="text" list="st-pms" data-f="' + c.k + '" data-id="' + r.id + '" value="' + esc(r[c.k + '_label']) + '"' + ro + ' title="' + esc(r[c.k + '_label']) + '">';
      case 'engineer': return '<input type="text" data-f="engineer" data-id="' + r.id + '" value="' + esc(r.engineer) + '"' + ro + '>';
      case 'start': return '<input type="date" data-f="start" data-id="' + r.id + '" value="' + esc(r.start || '') + '"' + ro + '>';
      case 'end': return '<input type="date" class="' + (r.end && r.end < today() && !r.completed ? 'overdue' : '') + '" data-f="end" data-id="' + r.id + '" value="' + esc(r.end || '') + '"' + ro + '>';
      case 'hours_left': return '<input type="number" step=".25" data-f="hours_left" data-id="' + r.id + '" value="' + r.hours_left + '"' + ro + ' style="text-align:right">';
      case 'percent': return '<input type="number" step="1" min="0" max="100" data-f="percent" data-id="' + r.id + '" value="' + r.percent + '"' + ro + ' style="text-align:right">';
      case 'notes': return '<input type="text" data-f="notes" data-id="' + r.id + '" value="' + esc(r.notes) + '" title="' + esc(r.notes) + '"' + ro + '>';
      case 'updated': return '<span class="muted" title="' + esc(upd(r)) + '">' + esc(upd(r)) + '</span>';
      case 'cv': return r.enrich ? PL.money(r.enrich.contract_value) : '<span class="muted">—</span>';
      case 'billed': return r.enrich ? PL.money(r.enrich.billed) : '<span class="muted">—</span>';
      case 'ptt': return r.enrich && r.enrich.ptt_hours != null ? PL.fmtNum(r.enrich.ptt_hours) + ' / ' + PL.fmtNum(r.enrich.budget_hours) + (r.enrich.hours_pct != null ? ' <span class="muted">(' + r.enrich.hours_pct + '%)</span>' : '') : '<span class="muted">—</span>';
      case 'rem': return r.enrich && r.enrich.remaining_hours != null ? '<span title="PM estimate as of ' + esc(r.enrich.remaining_updated || '') + '">' + PL.fmtNum(r.enrich.remaining_hours) + '</span>' : '<span class="muted">—</span>';
      case 'lastptt': return r.enrich && r.enrich.last_ptt ? PL.fmtDateShort(r.enrich.last_ptt) : '<span class="muted">—</span>';
      case 'estimator': return r.enrich ? esc(r.enrich.estimator || '—') : '<span class="muted">—</span>';
      case 'source': return '<span class="tag">' + esc(r.source) + '</span>';
    }
    return '';
  }
  function rowHtml(r, i) {
    const cols = visCols(); let left = 0;
    return '<tr data-id="' + r.id + '" data-ref="' + esc(r.project_number || '') + '" data-hl="' + r.id + '" class="' + (sel === r.id ? 'sel ' : '') + (r.completed ? 'done' : '') + '">' + cols.map(c => {
      const w = widths[c.k] || c.w; let cls = (c.fz ? 'fz' + (c.last ? ' last' : '') : '') + (c.num ? ' num' : '');
      const style = c.fz ? 'left:' + left + 'px' : ''; if (c.fz) left += w;
      return '<td class="' + cls + '" style="' + style + '">' + cell(r, c, i) + '</td>';
    }).join('') + '</tr>';
  }
  function render() {
    visible = visibleRows();
    const cols = visCols(); let left = 0;
    const colgroup = '<colgroup>' + cols.map(c => '<col style="width:' + (widths[c.k] || c.w) + 'px">').join('') + '</colgroup>';
    const head = '<thead><tr>' + cols.map(c => { const w = widths[c.k] || c.w; const st = c.fz ? 'left:' + left + 'px' : ''; if (c.fz) left += w;
      return '<th class="' + (c.fz ? 'fz' + (c.last ? ' last' : '') : '') + (sort && sort.k === c.k ? ' sorted' : '') + '" style="' + st + '" data-k="' + c.k + '" title="' + esc(c.tip || c.h) + '">' + (c.sort ? '<span class="sort">' + esc(c.h) + (sort && sort.k === c.k ? (sort.dir > 0 ? ' ▴' : ' ▾') : '') + '</span>' : esc(c.h)) + '<span class="rz" data-k="' + c.k + '" title="Drag to resize · double-click to auto-fit"></span></th>'; }).join('') + '</tr></thead>';
    const body = '<tbody>' + (visible.length ? visible.map(rowHtml).join('') : '<tr><td colspan="' + cols.length + '" class="empty">No rows match.</td></tr>') + '</tbody>';
    $('#st-table').innerHTML = colgroup + head + body;
    $('#st-table').style.zoom = zoom / 100;
    const total = viewRows().length;
    $('#st-foot').textContent = visible.length + ' visible / ' + total + ' ' + (view === 'completed' ? 'completed' : 'active') + ' rows' + (statusFilter ? ' · status = ' + statusFilter : '');
    $('#st-n-active').textContent = DS.rows.filter(r => !r.completed).length; $('#st-n-completed').textContent = DS.rows.filter(r => r.completed).length;
    document.querySelectorAll('#st-view button').forEach(b => b.classList.toggle('active', b.dataset.v === view));
    renderDivs(); updateButtons(); applyHeight();
  }
  function renderDivs() {
    const opts = (view === 'completed' ? [] : ['ALL']).concat(divisions);
    if (view === 'completed' && div === 'ALL') div = divisions.includes('040') ? '040' : divisions[0];
    $('#st-div').innerHTML = opts.map(d => { const n = DS.rows.filter(r => (view === 'completed') === !!r.completed && (d === 'ALL' || r.division === d)).length; return '<button data-d="' + d + '" class="' + (div === d ? 'active' : '') + '">' + (d === 'ALL' ? 'All' : d) + ' <span class="n">' + n + '</span></button>'; }).join('');
  }
  function updateButtons() { const on = !!(sel && rowsById.get(sel)); ['#st-edit', '#st-dup', '#st-del', '#st-send'].forEach(s => { const b = $(s); if (b) b.disabled = !on; }); }
  function rerow(r) { const tr = $('#st-table tr[data-id="' + r.id + '"]'); if (!tr) { render(); return; } const i = visible.findIndex(x => x.id === r.id); tr.outerHTML = rowHtml(r, i < 0 ? 0 : i); }
  function select(id) { sel = id; document.querySelectorAll('#st-table tr.sel').forEach(t => t.classList.remove('sel')); const tr = $('#st-table tr[data-id="' + id + '"]'); if (tr) tr.classList.add('sel'); updateButtons(); }
  function msg(t, cls) { const m = $('#st-msg'); m.textContent = t; m.className = 'pl-status ' + (cls || ''); if (t) setTimeout(() => { if (m.textContent === t) m.textContent = ''; }, 6000); }
  // ---- height / zoom / columns
  function applyHeight() { const w = $('#st-wrap'); const h = height === 'auto' ? Math.max(260, window.innerHeight - w.getBoundingClientRect().top - 34) : height; w.style.maxHeight = h + 'px'; $('#st-height').value = Math.min(1600, Math.max(260, h)); }
  function setZoom(z) { zoom = Math.max(45, Math.min(140, Math.round(z))); $('#st-zoom').value = zoom; $('#st-table').style.zoom = zoom / 100; PL.setPref('zoom', zoom); }
  function fitAll() { const natural = visCols().reduce((s, c) => s + (widths[c.k] || c.w), 0); const avail = $('#st-wrap').clientWidth - 2; setZoom(Math.max(45, Math.min(115, 100 * avail / natural))); }
  function renderColChooser() {
    $('#st-cols-body').innerHTML = COLS.filter(c => !['idx', 'done', 'task'].includes(c.k)).map(c => '<label style="display:flex;gap:6px;align-items:center;font-size:12.5px;padding:2px 0"><input type="checkbox" data-col="' + c.k + '"' + (hidden.includes(c.k) ? '' : ' checked') + '> ' + esc(c.h) + (c.grp === 'sl' ? ' <span class="chip">SL / PTT</span>' : '') + '</label>').join('');
  }
  function autoFit(k) {
    const c = COLS.find(x => x.k === k); if (!c) return;
    const cv = document.createElement('canvas').getContext('2d'); cv.font = '600 12.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
    let w = cv.measureText(c.h).width + 30;
    const getter = c.sort || (r => ''); cv.font = '12.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
    visible.slice(0, 400).forEach(r => { const v = k === 'task' ? r.name : (getter(r) == null ? '' : String(getter(r))); w = Math.max(w, cv.measureText(v).width + 34); });
    widths[k] = Math.round(Math.max(c.min || 40, Math.min(c.max || 900, w))); PL.setPref('widths', widths); render();
  }
  // ---- persistence
  async function save(id, field, value, input) {
    const r = rowsById.get(id); if (!r) return;
    const res = await PL.post(C.editUrl, {op: 'update', id, version: r.version, field, value});
    if (res.ok) { Object.assign(r, res.row); msg('Saved ' + field.replace('_', ' ') + ' · ' + PL.fmtDT(r.updated_at), 'ok');
      if (['phase_status', 'equipment_status', 'end', 'pm', 'pm2', 'division', 'name'].includes(field) || !document.activeElement || document.activeElement !== input) rerow(r);
      else { const u = $('#st-table tr[data-id="' + id + '"] td span.muted[title]'); if (u) u.textContent = upd(r); }
    } else { msg(res.error || 'Save failed', 'err'); PL.toast(res.error || 'Save failed', true); if (res.row) { Object.assign(r, res.row); rerow(r); } }
  }
  const debouncedSave = {};
  function onInput(e) {
    const el = e.target; if (!el.dataset.f || el.tagName === 'SELECT' || el.type === 'date') return;
    const key = el.dataset.id + ':' + el.dataset.f;
    (debouncedSave[key] = debouncedSave[key] || PL.debounce((id, f, v, inp) => save(id, f, v, inp), 650))(+el.dataset.id, el.dataset.f, el.value, el);
  }
  function onChange(e) {
    const el = e.target; if (!el.dataset.f) return;
    if (el.tagName === 'SELECT') el.style.cssText = PL.pillStyle(el.value, V);
    const key = el.dataset.id + ':' + el.dataset.f; if (debouncedSave[key]) { clearTimeout(); }
    save(+el.dataset.id, el.dataset.f, el.value, el);
  }
  async function toggleComplete(id) {
    const r = rowsById.get(id); const res = await PL.post(C.editUrl, {op: 'complete', id, version: r.version, completed: !r.completed});
    if (res.ok) { Object.assign(r, res.row); PL.toast(r.completed ? 'Moved to Completed Projects' : 'Returned to Active Projects'); render(); } else PL.toast(res.error, true);
  }
  async function newTask() {
    const res = await PL.post(C.editUrl, {op: 'create', fields: {division: div === 'ALL' ? '070' : div}});
    if (!res.ok) { PL.toast(res.error, true); return; }
    DS.rows.unshift(res.row); rowsById.set(res.row.id, res.row); view = 'active'; PL.setPref('view', view); sort = null; render(); select(res.row.id); openDrawer(res.row.id, true);
  }
  async function duplicate(id) { const r = rowsById.get(id); const res = await PL.post(C.editUrl, {op: 'duplicate', id, version: r.version}); if (!res.ok) { PL.toast(res.error, true); return; } DS.rows.unshift(res.row); rowsById.set(res.row.id, res.row); render(); select(res.row.id); PL.toast('Duplicated'); }
  async function del(id) { const r = rowsById.get(id); if (!(await PL.confirm('Delete this planner task?\n\n' + r.name, 'Delete'))) return; const res = await PL.post(C.editUrl, {op: 'delete', id, version: r.version}); if (!res.ok) { PL.toast(res.error, true); return; } DS.rows.splice(DS.rows.indexOf(r), 1); rowsById.delete(id); if (sel === id) sel = null; closeDrawer(); render(); PL.toast('Deleted'); }
  async function send(id) { const r = rowsById.get(id); const res = await PL.post(C.sendUrl, {id}); if (res.row) { Object.assign(r, res.row); rerow(r); } msg(res.message || res.error, res.ok ? 'ok' : 'err'); PL.toast(res.message || res.error, !res.ok); }
  // ---- drawer
  let drawerId = null;
  function field(label, inner, full, req) { return '<label class="' + (full ? 'full' : '') + (req ? ' req' : '') + '">' + label + inner + '</label>'; }
  const statusOpts = v => V.keys.map(s => '<option value="' + esc(s) + '"' + (s === v ? ' selected' : '') + '>' + (s || '— none —') + '</option>').join('') + (V.keys.includes(v) ? '' : '<option selected>' + esc(v) + '</option>');
  function openDrawer(id, edit) {
    const r = rowsById.get(id); if (!r) return; drawerId = id; select(id);
    $('#st-drawer-title').textContent = (r.division ? r.division + ' · ' : '') + r.name;
    const en = r.enrich;
    const info = en ? '<div class="note" style="margin-bottom:10px">SL <a href="' + en.url + '" target="_blank">' + esc(en.num) + '</a> · ' + esc(en.customer || '') + ' · contract ' + PL.money(en.contract_value) + ' · billed ' + PL.money(en.billed) + ' · PTT ' + PL.fmtNum(en.ptt_hours) + ' / ' + PL.fmtNum(en.budget_hours) + ' h · remaining ' + PL.fmtNum(en.remaining_hours) + ' h · last entry ' + (en.last_ptt ? PL.fmtDate(en.last_ptt) : '—') + (en.estimator ? ' · estimator ' + esc(en.estimator) : '') + '</div>' : '<div class="note" style="margin-bottom:10px">No SL project linked' + (r.project_number ? ' (number ' + esc(r.project_number) + ' not in SL)' : '') + ' — type the job number below to link one.</div>';
    if (edit || !C.canWrite) {
      $('#st-drawer-body').innerHTML = info + '<form class="pl-form" id="st-form">' +
        field('Task name', '<input name="name" value="' + esc(r.name) + '" required>', true, true) +
        field('Division', '<select name="division">' + divisions.map(d => '<option' + (d === r.division ? ' selected' : '') + '>' + d + '</option>').join('') + '</select>') +
        field('Project number (SL)', '<input name="project_number" value="' + esc(r.project_number || '') + '" placeholder="6 digits">') +
        field('Phase Complete Status', '<select name="phase_status" class="st" style="' + PL.pillStyle(r.phase_status, V) + '">' + statusOpts(r.phase_status) + '</select>') +
        field('Equipment Complete Status V2', '<select name="equipment_status" class="st" style="' + PL.pillStyle(r.equipment_status, V) + '">' + statusOpts(r.equipment_status) + '</select>') +
        field('PM', '<input name="pm" list="st-pms" value="' + esc(r.pm_label) + '">') + field('PM2', '<input name="pm2" list="st-pms" value="' + esc(r.pm2_label) + '">') +
        field('Engineer', '<input name="engineer" value="' + esc(r.engineer) + '">') + field('Task #', '<input name="task_num" type="number" value="' + (r.task_num == null ? '' : r.task_num) + '">') +
        field('Start date', '<input name="start" type="date" value="' + esc(r.start || '') + '">') + field('End date (critical stop)', '<input name="end" type="date" value="' + esc(r.end || '') + '">') +
        field('Hours left (union)', '<input name="hours_left" type="number" step=".25" value="' + r.hours_left + '">') + field('% complete', '<input name="percent" type="number" min="0" max="100" value="' + r.percent + '">') +
        field('Notes', '<textarea name="notes">' + esc(r.notes) + '</textarea>', true) +
        (C.canWrite ? '' : '<div class="note full">Read-only: your role has no planning.write capability.</div>') + '</form>';
      $('#st-drawer-foot').innerHTML = C.canWrite ? '<button class="btn primary" data-act="save">Save changes</button><button class="btn" data-act="view">Cancel</button>' : '<button class="btn" data-act="close">Close</button>';
      $('#st-form').querySelectorAll('select.st').forEach(s => s.addEventListener('change', () => s.style.cssText = PL.pillStyle(s.value, V)));
    } else {
      $('#st-drawer-body').innerHTML = info + '<dl class="kv dense">' +
        '<dt>Phase status</dt><dd>' + PL.pill(r.phase_status, V) + '</dd><dt>Equipment status</dt><dd>' + PL.pill(r.equipment_status, V) + '</dd>' +
        '<dt>PM</dt><dd>' + esc(r.pm_label || '—') + (r.pm2_label ? ' / ' + esc(r.pm2_label) : '') + '</dd><dt>Engineer</dt><dd>' + esc(r.engineer || '—') + '</dd>' +
        '<dt>Start → end</dt><dd>' + (PL.fmtDate(r.start) || '—') + ' → ' + (PL.fmtDate(r.end) || '—') + '</dd><dt>Hours left</dt><dd>' + r.hours_left + ' h · ' + r.percent + '% done</dd>' +
        (r.foreman ? '<dt>Foreman</dt><dd>' + esc(r.foreman) + '</dd>' : '') + (r.site_contact ? '<dt>Site contact</dt><dd>' + esc(r.site_contact) + '</dd>' : '') + (r.assigned_to ? '<dt>Assigned to</dt><dd>' + esc(r.assigned_to) + '</dd>' : '') +
        '<dt>Source</dt><dd>' + esc(r.source) + (r.sent_to_scheduler_at ? ' · sent to scheduler ' + PL.fmtDT(r.sent_to_scheduler_at) : '') + '</dd><dt>Last updated</dt><dd>' + esc(upd(r)) + '</dd></dl>' +
        '<h3 style="margin:12px 0 4px">Notes</h3><div style="white-space:pre-wrap;font-size:13px">' + (esc(r.notes) || '<span class="muted">—</span>') + '</div>' +
        '<h3 style="margin:12px 0 4px">History</h3><div id="st-hist" class="note">loading…</div>';
      $('#st-drawer-foot').innerHTML = C.canWrite ? '<button class="btn primary" data-act="edit">Edit task</button><button class="btn" data-act="complete">' + (r.completed ? 'Return to Active' : 'Owner project complete') + '</button><button class="btn" data-act="dup">Duplicate</button><button class="btn" data-act="send" title="Turn into a Resource Scheduler project">Turn into scheduler project</button><button class="btn" data-act="del" style="color:var(--bad)">Delete</button>' : '<button class="btn" data-act="close">Close</button>';
      fetch(C.dataUrl + '?history=' + id).then(r => r.json()).then(h => { const el = $('#st-hist'); if (!el) return; el.innerHTML = h.history.length ? '<table class="data dense"><tbody>' + h.history.map(x => '<tr><td class="muted" style="white-space:nowrap">' + esc(PL.fmtDT(x.at)) + '</td><td>' + esc(x.by || '—') + '</td><td class="mono">' + esc(x.field) + '</td><td class="muted">' + esc((x.old || '').slice(0, 60)) + '</td><td>' + esc((x.new || '').slice(0, 80)) + '</td></tr>').join('') + '</tbody></table>' : 'No changes recorded.'; }).catch(() => {});
    }
    $('#st-drawer').classList.add('open'); $('#st-drawer-bg').classList.add('open'); document.body.style.overflow = 'hidden';
  }
  function closeDrawer() { $('#st-drawer').classList.remove('open'); $('#st-drawer-bg').classList.remove('open'); document.body.style.overflow = ''; drawerId = null; }
  async function saveDrawer() {
    const r = rowsById.get(drawerId); const f = $('#st-form'); const fields = {};
    for (const el of f.elements) if (el.name) fields[el.name] = el.value;
    if (!fields.name.trim()) fields.name = r.name;
    const res = await PL.post(C.editUrl, {op: 'update', id: r.id, version: r.version, fields});
    if (res.ok) { Object.assign(r, res.row); render(); PL.toast('Saved'); openDrawer(r.id, false); } else { PL.toast(res.error, true); if (res.row) { Object.assign(r, res.row); render(); } }
  }
  // ---- hover card
  function hover(id, target) {
    const r = rowsById.get(id); const en = r.enrich;
    PL.hoverCard(target, '<b>' + esc(r.name) + '</b><div class="muted" style="margin-bottom:6px">' + esc(r.project_number || 'no SL number') + (en ? ' · ' + esc(en.customer) + ' · ' + esc(en.lifecycle.replace(/_/g, ' ')) : '') + '</div>' +
      (en ? '<dl class="kv dense"><dt>Contract value</dt><dd>' + PL.money(en.contract_value) + '</dd><dt>Billed</dt><dd>' + PL.money(en.billed) + '</dd><dt>PTT hours / budget</dt><dd>' + PL.fmtNum(en.ptt_hours) + ' / ' + PL.fmtNum(en.budget_hours) + (en.hours_pct != null ? ' (' + en.hours_pct + '%)' : '') + '</dd><dt>PM remaining (PTT)</dt><dd>' + PL.fmtNum(en.remaining_hours) + ' h' + (en.remaining_updated ? ' as of ' + PL.fmtDate(en.remaining_updated) : '') + '</dd><dt>PTT % complete</dt><dd>' + (en.pct_ptt == null ? '—' : en.pct_ptt + '%') + '</dd><dt>Last PTT entry</dt><dd>' + (en.last_ptt ? PL.fmtDate(en.last_ptt) : '—') + '</dd><dt>SL PM</dt><dd>' + esc(en.pm_sl || '—') + '</dd><dt>Estimator</dt><dd>' + esc(en.estimator || '—') + '</dd></dl>' : '<div class="muted">No SL project linked — nothing to enrich.</div>') +
      '<div class="muted" style="margin-top:6px">Board: ' + esc(r.pm_label || '—') + ' · ' + r.hours_left + ' h left · ' + r.percent + '% · stop ' + (PL.fmtDate(r.end) || '—') + '</div>');
  }
  // ---- events
  const tbl = $('#st-table');
  tbl.addEventListener('input', onInput);
  tbl.addEventListener('change', onChange);
  tbl.addEventListener('focusin', e => { const tr = e.target.closest('tr[data-id]'); if (tr) select(+tr.dataset.id); });
  tbl.addEventListener('click', e => {
    const b = e.target.closest('button[data-act]'); const tr = e.target.closest('tr[data-id]');
    if (b) { const id = +b.dataset.id; if (b.dataset.act === 'complete') toggleComplete(id); else if (b.dataset.act === 'drawer') openDrawer(id, false); return; }
    const th = e.target.closest('th[data-k]');
    if (th && e.target.classList.contains('sort')) { const k = th.dataset.k; sort = sort && sort.k === k ? (sort.dir > 0 ? {k, dir: -1} : null) : {k, dir: 1}; PL.setPref('sort', sort); render(); return; }
    if (tr && !e.target.closest('input, select, button, a')) select(+tr.dataset.id);
  });
  tbl.addEventListener('dblclick', e => { const rz = e.target.closest('.rz'); if (rz) { autoFit(rz.dataset.k); e.preventDefault(); } });
  tbl.addEventListener('mouseover', e => { const t = e.target.closest('[data-hover]'); if (t) hover(+t.dataset.hover, t); });
  tbl.addEventListener('mouseout', e => { if (e.target.closest('[data-hover]')) PL.hideHover(); });
  // column resize (pointer events, zoom-compensated)
  let drag = null;
  tbl.addEventListener('pointerdown', e => { const rz = e.target.closest('.rz'); if (!rz) return; const c = COLS.find(x => x.k === rz.dataset.k); drag = {k: c.k, x: e.clientX, w: widths[c.k] || c.w, c}; rz.classList.add('on'); rz.setPointerCapture(e.pointerId); e.preventDefault(); });
  tbl.addEventListener('pointermove', e => { if (!drag) return; const w = Math.round(Math.max(drag.c.min || 40, Math.min(drag.c.max || 1200, drag.w + (e.clientX - drag.x) / (zoom / 100)))); widths[drag.k] = w; const i = visCols().findIndex(c => c.k === drag.k); const col = tbl.querySelectorAll('col')[i]; if (col) col.style.width = w + 'px'; });
  tbl.addEventListener('pointerup', () => { if (!drag) return; PL.setPref('widths', widths); drag = null; render(); });
  $('#st-view').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; view = b.dataset.v; if (view === 'active') div = 'ALL'; PL.setPref('view', view); PL.setPref('div', div); render(); });
  $('#st-div').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; div = b.dataset.d; PL.setPref('div', div); render(); });
  $('#st-q').addEventListener('input', PL.debounce(e => { q = e.target.value.trim().toLowerCase(); render(); }, 150));
  if (C.canWrite) { $('#st-new').onclick = newTask; $('#st-edit').onclick = () => openDrawer(sel, true); $('#st-dup').onclick = () => duplicate(sel); $('#st-del').onclick = () => del(sel); $('#st-send').onclick = () => send(sel); }
  $('#st-drawer-close').onclick = closeDrawer; $('#st-drawer-bg').onclick = closeDrawer;
  $('#st-drawer-foot').addEventListener('click', e => { const b = e.target.closest('button[data-act]'); if (!b) return; const a = b.dataset.act;
    if (a === 'save') saveDrawer(); else if (a === 'view') openDrawer(drawerId, false); else if (a === 'edit') openDrawer(drawerId, true); else if (a === 'close') closeDrawer();
    else if (a === 'complete') toggleComplete(drawerId).then(() => drawerId && openDrawer(drawerId, false)); else if (a === 'dup') duplicate(drawerId); else if (a === 'del') del(drawerId); else if (a === 'send') send(drawerId); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawerId) closeDrawer(); });
  $('#st-zoom').addEventListener('input', e => setZoom(+e.target.value)); $('#st-zoom-minus').onclick = () => setZoom(zoom - 5); $('#st-zoom-plus').onclick = () => setZoom(zoom + 5); $('#st-fit').onclick = fitAll;
  $('#st-height').addEventListener('input', e => { height = +e.target.value; PL.setPref('height', height); applyHeight(); }); $('#st-height-auto').onclick = () => { height = 'auto'; PL.setPref('height', 'auto'); applyHeight(); };
  $('#st-reset').onclick = () => { widths = {}; hidden = DEFAULT_HIDDEN.slice(); height = 'auto'; sort = null; PL.setPref('widths', null); PL.setPref('hidden', null); PL.setPref('height', null); PL.setPref('sort', null); setZoom(100); renderColChooser(); render(); fitAll(); };
  $('#st-cols-body').addEventListener('change', e => { const cb = e.target.closest('input[data-col]'); if (!cb) return; hidden = cb.checked ? hidden.filter(k => k !== cb.dataset.col) : hidden.concat([cb.dataset.col]); PL.setPref('hidden', hidden); render(); });
  window.addEventListener('resize', PL.debounce(() => { if (height === 'auto') applyHeight(); }, 90));
  // PM datalist
  const dl = document.createElement('datalist'); dl.id = 'st-pms'; dl.innerHTML = pmNames.map(n => '<option value="' + esc(n) + '">').join(''); document.body.appendChild(dl);
  // ---- deep link (?hl=<row id | project number>): switch view / division so the row is on screen; base.html flashes it
  if (C.hl) { const t = DS.rows.find(r => String(r.id) === C.hl) || DS.rows.find(r => r.project_number === C.hl) || DS.rows.find(r => r.job_key === C.hl) || DS.rows.find(r => r.name.toLowerCase().includes(C.hl.toLowerCase()));
    if (t) { view = t.completed ? 'completed' : 'active'; div = t.completed ? t.division : 'ALL'; sel = t.id; } else { $('#st-q').value = C.hl; q = C.hl.toLowerCase(); msg('No exact status record for "' + C.hl + '" yet — showing the search instead.'); } }
  if (C.div && divisions.includes(C.div)) div = C.div;
  renderColChooser(); $('#st-zoom').value = zoom; render(); $('#st-table').style.zoom = zoom / 100;
  if (C.status) msg('Showing rows with status ' + C.status + ' — clear the URL filter to see all.');
})();
