/* BOM & labor approvals (spec §6.5, BA-01…BA-08): cards as filters, search / status / type, sortable table newest-first,
   create / edit drawer with attachments, approve / reopen (capability-gated server-side), delete, CSV of the view. */
(function () {
  const DS = JSON.parse(document.getElementById('ap-data').textContent), C = window.AP_CONF, esc = PL.esc, $ = s => document.querySelector(s);
  const reqs = DS.requests, byId = new Map(reqs.map(r => [r.id, r]));
  let filter = C.init.filter || 'all', q = C.init.q || '', st = '', kind = '', sort = PL.pref('sort', {k: 'submitted_at', dir: -1}), drawerId = null;
  if (filter === 'pending') st = 'pending'; if (filter === 'approved') st = 'approved';
  $('#ap-q').value = q;
  const today = () => PL.todayISO();
  const overdue = r => r.awaiting && r.needed_by && r.needed_by < today();
  function filtered() {
    let rows = reqs.slice();
    if (st === 'pending') rows = rows.filter(r => r.awaiting); else if (st === 'approved') rows = rows.filter(r => r.status === 'approved');
    if (kind) rows = rows.filter(r => r.kind === kind);
    if (filter === 'attachments') rows = rows.filter(r => r.n_files); else if (filter === 'overdue') rows = rows.filter(overdue);
    if (q) { const s = q.toLowerCase(); rows = rows.filter(r => [r.project_name, r.project_number, r.requested_by, r.approver, r.department, r.notes, r.kind, r.status_label].join(' ').toLowerCase().includes(s)); }
    const g = {project: r => r.project_name, kind: r => r.kind, requested_by: r => r.requested_by, submitted_at: r => r.submitted_at, needed_by: r => r.needed_by || '9999', status: r => r.status_label, approver: r => r.approver, files: r => r.n_files};
    return PL.sortBy(rows, null, sort.dir, g[sort.k] || g.submitted_at);
  }
  function render() {
    const rows = filtered();
    const k = {total: reqs.length, pending: reqs.filter(r => r.awaiting).length, approved: reqs.filter(r => r.status === 'approved').length, files: reqs.reduce((s, r) => s + r.n_files, 0), withFiles: reqs.filter(r => r.n_files).length, overdue: reqs.filter(overdue).length};
    const card = (key, l, v, sub, cls, tip) => '<div class="kpi card tight click ' + cls + (filter === key ? ' on' : '') + '" data-filter="' + key + '" title="' + esc(tip) + '"><div class="label">' + l + '</div><div class="value">' + v + '</div><div class="sub">' + sub + '</div></div>';
    $('#ap-kpi').innerHTML = card('all', 'Total requests', k.total, 'all time', '', 'Every request (click to clear filters)') + card('pending', 'Pending approval', k.pending, 'awaiting a decision (incl. reopened)', 'amber', 'Pending or reopened requests') + card('approved', 'Approved', k.approved, 'decided', 'green', 'Approved requests') +
      card('attachments', 'Attached files', k.files, k.withFiles + ' request' + (k.withFiles === 1 ? '' : 's') + ' with files', '', 'Count of files; the filter shows the requests that carry them') + card('overdue', 'Overdue on needed-by', k.overdue, 'still pending past the date', 'red', 'Awaiting a decision and the needed-by date is before today (new in PCA)');
    const th = (key, l, cls) => '<th class="' + (cls || '') + (sort.k === key ? ' sorted' : '') + '" data-sort="' + key + '" style="cursor:pointer" title="Click to sort">' + l + (sort.k === key ? (sort.dir > 0 ? ' ▴' : ' ▾') : '') + '</th>';
    $('#ap-table').innerHTML = '<thead><tr>' + th('project', 'Project') + th('kind', 'Request') + th('requested_by', 'Requested by') + th('submitted_at', 'Submitted') + th('needed_by', 'Needed by') + th('files', 'Attachments') + '<th>Notes</th>' + th('status', 'Status') + th('approver', 'Approver') + '<th>Actions</th></tr></thead><tbody>' +
      (rows.length ? rows.map(r => '<tr class="rowlink" data-id="' + r.id + '" tabindex="0"><td><b>' + esc(r.project_name) + '</b><div class="muted">' + (r.project_number ? (r.project_url ? '<a href="' + r.project_url + '" onclick="event.stopPropagation()">' + esc(r.project_number) + '</a>' : esc(r.project_number)) : 'No project number') + '</div></td>' +
        '<td><span class="ap-chip ' + r.kind.toLowerCase() + '" data-kind="' + r.kind + '" title="Filter by type">' + r.kind + '</span></td><td>' + esc(r.requested_by || '—') + '<div class="muted">' + esc(r.department || 'No department') + '</div></td>' +
        '<td title="' + esc(r.submitted_at) + '">' + esc(PL.fmtDT(r.submitted_at)) + '</td><td' + (overdue(r) ? ' style="color:var(--bad);font-weight:600" title="Past the needed-by date and still pending"' : '') + '>' + (PL.fmtDate(r.needed_by) || '—') + '</td>' +
        '<td class="ap-files">' + (r.attachments.length ? r.attachments.map(a => '<a href="' + a.url + '" title="' + esc(a.name) + ' · ' + Math.round(a.size / 1024) + ' KB" onclick="event.stopPropagation()">' + esc(a.name) + '</a>').join('') : '<span class="muted">No files</span>') + '</td>' +
        '<td><div class="ap-notes" title="' + esc(r.notes) + '">' + esc(r.notes.length > 180 ? r.notes.slice(0, 180) + '…' : r.notes) + '</div></td>' +
        '<td><span class="ap-chip ' + r.status + '" data-status="' + (r.awaiting ? 'pending' : 'approved') + '" title="Filter by status">● ' + esc(r.status_label) + '</span><div class="muted">' + (r.status === 'approved' ? 'Approved by ' + esc(r.decided_by || '?') + ' ' + esc(PL.fmtDate(r.decided_at)) : r.status === 'reopened' ? 'Reopened' : 'Pending approval') + '</div></td>' +
        '<td><button class="pm-chip" data-approver="' + esc(r.approver) + '" title="Filter by approver">' + esc(r.approver || 'Not assigned') + '</button></td>' +
        '<td class="ap-actions">' + (C.canWrite ? '<button class="btn small" data-edit="' + r.id + '">Edit</button>' : '') + (C.canApprove ? (r.awaiting ? '<button class="btn small primary" data-decide="approve" data-id="' + r.id + '" title="Approve as ' + esc(C.who) + '">Approve</button>' : '<button class="btn small" data-decide="reopen" data-id="' + r.id + '">Reopen</button>') : '') + (C.canWrite ? '<button class="btn small" data-del="' + r.id + '" style="color:var(--bad)">Delete</button>' : '') + '</td></tr>').join('') : '<tr><td colspan="10" class="empty">No requests found</td></tr>') + '</tbody>';
    const p = new URLSearchParams(); if (q) p.set('q', q); if (st) p.set('status', st); if (kind) p.set('type', kind); if (filter === 'attachments' || filter === 'overdue') p.set('filter', filter);
    $('#ap-csv').href = C.exportUrl + (p.toString() ? '?' + p : '');
    $('#ap-status').value = st; $('#ap-type').value = kind;
  }
  function field(l, inner, full, req) { return '<label class="' + (full ? 'full' : '') + (req ? ' req' : '') + '">' + l + inner + '</label>'; }
  function openDrawer(id) {
    const r = id ? byId.get(id) : null; drawerId = id || 0;
    $('#ap-drawer-title').textContent = r ? 'Edit request · ' + r.project_name : 'New request';
    const approvers = DS.approvers.map(a => a.name);
    $('#ap-drawer-body').innerHTML = '<form class="pl-form" id="ap-form" enctype="multipart/form-data">' +
      field('Project name', '<input name="project_name" maxlength="150" required value="' + esc(r ? r.project_name : '') + '">', true, true) +
      field('Project number', '<input name="project_number" maxlength="60" placeholder="SL job number" value="' + esc(r ? r.project_number : '') + '">') +
      field('Request type', '<select name="kind" required><option value="">Select…</option><option' + (r && r.kind === 'BOM' ? ' selected' : '') + '>BOM</option><option' + (r && r.kind === 'Labor' ? ' selected' : '') + '>Labor</option></select>', false, true) +
      field('Requested by', '<input value="' + esc(r ? r.requested_by : C.who) + '" disabled title="The signed-in user — no typed names">') +
      field('Approver', '<input name="approver" list="ap-approvers" placeholder="Person who needs to approve" value="' + esc(r ? r.approver : '') + '">') +
      field('Needed by', '<input name="needed_by" type="date" value="' + esc(r ? (r.needed_by || '') : '') + '">') +
      field('Department / team', '<input name="department" maxlength="100" value="' + esc(r ? r.department : '') + '">') +
      field('Request notes', '<textarea name="notes" maxlength="4000">' + esc(r ? r.notes : '') + '</textarea>', true) +
      (r && r.attachments.length ? '<div class="full"><div class="muted" style="font-size:11.5px;font-weight:600">Current attachments</div><div class="file-list">' + r.attachments.map(a => '<label><a href="' + a.url + '">' + esc(a.name) + '</a> <span class="muted">' + Math.round(a.size / 1024) + ' KB</span> <button type="button" class="btn small" data-rmatt="' + a.id + '">Remove</button></label>').join('') + '</div></div>' : '') +
      field('Add attachments', '<input name="files" type="file" multiple>', true) + '<div class="err" id="ap-err"></div></form>';
    $('#ap-drawer-foot').innerHTML = '<button class="btn primary" id="ap-save">' + (r ? 'Save changes' : 'Submit for approval') + '</button><button class="btn" id="ap-cancel">Cancel</button>';
    let dl = document.getElementById('ap-approvers'); if (!dl) { dl = document.createElement('datalist'); dl.id = 'ap-approvers'; document.body.appendChild(dl); } dl.innerHTML = approvers.map(n => '<option value="' + esc(n) + '">').join('');
    $('#ap-drawer').classList.add('open'); $('#ap-drawer-bg').classList.add('open'); document.body.style.overflow = 'hidden';
    setTimeout(() => $('#ap-form [name=project_name]').focus(), 40);
  }
  function closeDrawer() { $('#ap-drawer').classList.remove('open'); $('#ap-drawer-bg').classList.remove('open'); document.body.style.overflow = ''; drawerId = null; }
  async function save() {
    const f = $('#ap-form'); if (!f.reportValidity()) return;
    const fd = new FormData(f); const r = drawerId ? byId.get(drawerId) : null;
    fd.append('op', r ? 'update' : 'create'); if (r) { fd.append('id', r.id); fd.append('version', r.version); }
    const res = await PL.post(C.editUrl, fd, true);
    if (!res.ok) { $('#ap-err').textContent = res.error || 'Save failed'; return; }
    if (r) Object.assign(r, res.request); else { reqs.unshift(res.request); byId.set(res.request.id, res.request); }
    closeDrawer(); render(); PL.toast(r ? 'Request updated.' : 'Request submitted for approval.');
  }
  async function decide(id, action) {
    const r = byId.get(id); const res = await PL.post(C.decideUrl, {id, action, version: r.version});
    if (res.ok) { Object.assign(r, res.request); render(); PL.toast(action === 'approve' ? 'Approved as ' + C.who : 'Reopened'); } else PL.toast(res.error, true);
  }
  async function del(id) { const r = byId.get(id); if (!(await PL.confirm('Delete the request for "' + r.project_name + '"? This cannot be undone.', 'Delete'))) return; const res = await PL.post(C.editUrl, {op: 'delete', id, version: r.version}); if (res.ok) { reqs.splice(reqs.indexOf(r), 1); byId.delete(id); render(); PL.toast('Deleted'); } else PL.toast(res.error, true); }
  $('#ap-kpi').addEventListener('click', e => { const c = e.target.closest('[data-filter]'); if (!c) return; filter = c.dataset.filter; st = filter === 'pending' ? 'pending' : filter === 'approved' ? 'approved' : ''; kind = ''; q = ''; $('#ap-q').value = ''; render(); });
  $('#ap-q').addEventListener('input', PL.debounce(e => { q = e.target.value.trim(); filter = 'custom'; render(); }, 150));
  $('#ap-status').addEventListener('change', e => { st = e.target.value; filter = 'custom'; render(); });
  $('#ap-type').addEventListener('change', e => { kind = e.target.value; filter = 'custom'; render(); });
  $('#ap-table').addEventListener('click', e => { const t = e.target;
    const th = t.closest('th[data-sort]'); if (th) { sort = sort.k === th.dataset.sort ? {k: sort.k, dir: -sort.dir} : {k: th.dataset.sort, dir: th.dataset.sort === 'submitted_at' ? -1 : 1}; PL.setPref('sort', sort); render(); return; }
    const kc = t.closest('[data-kind]'); if (kc) { kind = kind === kc.dataset.kind ? '' : kc.dataset.kind; filter = 'custom'; render(); return; }
    const sc = t.closest('[data-status]'); if (sc) { st = st === sc.dataset.status ? '' : sc.dataset.status; filter = 'custom'; render(); return; }
    const ac = t.closest('[data-approver]'); if (ac) { q = ac.dataset.approver; $('#ap-q').value = q; filter = 'custom'; render(); return; }
    const ed = t.closest('[data-edit]'); if (ed) { openDrawer(+ed.dataset.edit); return; }
    const dc = t.closest('[data-decide]'); if (dc) { decide(+dc.dataset.id, dc.dataset.decide); return; }
    const dl = t.closest('[data-del]'); if (dl) { del(+dl.dataset.del); return; }
    const tr = t.closest('tr[data-id]'); if (tr && !t.closest('a, button')) openDrawer(+tr.dataset.id); });
  $('#ap-table').addEventListener('keydown', e => { const tr = e.target.closest('tr[data-id]'); if (tr && (e.key === 'Enter' || e.key === ' ') && e.target === tr) { e.preventDefault(); openDrawer(+tr.dataset.id); } });
  $('#ap-drawer-foot').addEventListener('click', e => { if (e.target.id === 'ap-save') save(); else if (e.target.id === 'ap-cancel') closeDrawer(); });
  $('#ap-drawer-body').addEventListener('click', async e => { const b = e.target.closest('[data-rmatt]'); if (!b) return; const r = byId.get(drawerId); const res = await PL.post(C.editUrl, {op: 'remove_attachment', id: r.id, version: r.version, attachment_id: +b.dataset.rmatt}); if (res.ok) { Object.assign(r, res.request); openDrawer(r.id); render(); } else PL.toast(res.error, true); });
  $('#ap-drawer-close').onclick = closeDrawer; $('#ap-drawer-bg').onclick = closeDrawer;
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && drawerId != null) closeDrawer(); });
  if (C.canWrite) $('#ap-new').onclick = () => openDrawer(0);
  render();
})();
