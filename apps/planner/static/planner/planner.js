/* Planner boards (apps/planner): picker → one plan as kanban / list / change log, rendered from the JSON endpoint.
   Every toggle is remembered through PCA.pref (keys 'planner.*', scoped to the Production nav). "Today" is the live
   US-Central clock (re-evaluated every minute), never the load time. */
(function () {
  'use strict';
  const CFG = window.PL_CFG || {};
  if (!CFG.connected) return;
  const $ = (id) => document.getElementById(id);
  const pref = (k, d) => (typeof PCA !== 'undefined' && PCA.pref) ? PCA.pref('planner.' + k, d) : d;
  const setPref = (k, v) => { if (typeof PCA !== 'undefined' && PCA.setPref) PCA.setPref('planner.' + k, v); };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const money = (v) => (typeof PCA !== 'undefined' && PCA.money) ? PCA.money(v) : String(v);
  const hours = (v) => v == null ? '—' : Math.round(v).toLocaleString() + ' h';

  // ---- live clock (Central calendar date)
  const todayISO = () => new Date().toLocaleDateString('en-CA', {timeZone: 'America/Chicago'});
  let TODAY = todayISO();
  const fmtDate = (iso) => {
    if (!iso) return '';
    const d = new Date(iso + 'T12:00:00');
    const y = d.getFullYear() === new Date().getFullYear() ? '' : ', ' + d.getFullYear();
    return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) + y;
  };
  const fmtWhen = (iso) => iso ? new Date(iso).toLocaleString('en-US', {timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';
  const rel = (iso) => {
    if (!iso) return 'no changes seen';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 3600) return 'changed ' + Math.max(1, Math.round(s / 60)) + ' min ago';
    if (s < 86400) return 'changed ' + Math.round(s / 3600) + ' h ago';
    if (s < 86400 * 30) return 'changed ' + Math.round(s / 86400) + ' d ago';
    return 'changed ' + fmtDate(iso.slice(0, 10));
  };
  const daysTo = (iso) => Math.round((new Date(iso + 'T12:00:00') - new Date(TODAY + 'T12:00:00')) / 86400000);
  const isOverdue = (t) => !!t.due && t.percent < 100 && t.due < TODAY;
  const isSoon = (t) => !!t.due && t.percent < 100 && !isOverdue(t) && daysTo(t.due) <= 3;

  document.querySelectorAll('.pl-rel').forEach(el => { el.textContent = rel(el.dataset.at); });

  // ---- state
  const S = {
    plan: null, data: null, view: pref('view', 'board'), since: pref('since', 14),
    sort: pref('sort', [['due', 'asc']]),
    f: Object.assign({division: '', assignee: '', label: '', state: 'open', overdue: false, mine: false, linked: false, q: ''}, pref('filters', {}) || {}),
  };
  const F = {division: $('f-division'), assignee: $('f-assignee'), label: $('f-label'), state: $('f-state'), overdue: $('f-overdue'), mine: $('f-mine'), linked: $('f-linked'), q: $('f-q')};

  function saveFilters() { setPref('filters', S.f); }
  function readFilters() {
    S.f = {division: F.division.value, assignee: F.assignee.value, label: F.label.value, state: F.state.value,
           overdue: F.overdue.checked, mine: F.mine.checked, linked: F.linked.checked, q: F.q.value.trim()};
    saveFilters();
  }
  function writeFilters() {
    F.division.value = S.f.division || ''; F.assignee.value = S.f.assignee || ''; F.label.value = S.f.label || ''; F.state.value = S.f.state || '';
    F.overdue.checked = !!S.f.overdue; F.mine.checked = !!S.f.mine; F.linked.checked = !!S.f.linked; F.q.value = S.f.q || '';
    if (F.division.value !== (S.f.division || '')) F.division.value = '';   // option vanished with the plan
    if (F.assignee.value !== (S.f.assignee || '')) F.assignee.value = '';
    if (F.label.value !== (S.f.label || '')) F.label.value = '';
  }
  Object.values(F).forEach(el => el.addEventListener(el.type === 'search' ? 'input' : 'change', () => { readFilters(); render(); }));
  $('f-reset').addEventListener('click', () => { S.f = {division: '', assignee: '', label: '', state: '', overdue: false, mine: false, linked: false, q: ''}; saveFilters(); writeFilters(); render(); });
  $('f-since').value = String(S.since);
  $('f-since').addEventListener('change', () => { S.since = parseInt($('f-since').value, 10) || 14; setPref('since', S.since); load(S.plan, true); });
  document.querySelectorAll('.pl-view').forEach(b => b.addEventListener('click', () => { S.view = b.dataset.view; setPref('view', S.view); render(); }));
  $('pl-change').addEventListener('click', () => { const p = $('pl-picker'); p.open = true; p.scrollIntoView({behavior: 'smooth', block: 'start'}); });

  // ---- picker
  document.querySelectorAll('.pl-plan[data-plan]').forEach(a => a.addEventListener('click', (e) => {
    e.preventDefault();
    const id = a.dataset.plan;
    history.pushState(null, '', '?plan=' + encodeURIComponent(id));
    if (typeof PCA !== 'undefined' && PCA.rememberFilters) PCA.rememberFilters();
    load(id);
  }));
  window.addEventListener('popstate', () => { const id = new URLSearchParams(location.search).get('plan'); if (id) load(id); });

  function markCurrent(id) {
    document.querySelectorAll('.pl-plan').forEach(a => a.classList.toggle('current', a.dataset.plan === id));
  }

  // ---- load
  async function load(id, keepOpen) {
    S.plan = id; setPref('plan', id); markCurrent(id);
    const wrap = $('pl-board-wrap');
    wrap.hidden = false;
    $('pl-plan-title').textContent = 'Loading…';
    let r;
    try {
      r = await fetch(CFG.data + '?plan=' + encodeURIComponent(id) + '&since=' + S.since, {credentials: 'same-origin'});
    } catch (e) { $('pl-plan-title').textContent = 'Could not load the board'; return; }
    if (!r.ok) { $('pl-plan-title').textContent = r.status === 404 ? 'That board no longer exists — pick another' : 'Could not load the board (' + r.status + ')'; $('pl-plan-sub').textContent = ''; return; }
    S.data = await r.json();
    if (!keepOpen) $('pl-picker').open = false;
    const p = S.data.plan;
    $('pl-plan-title').textContent = p.title;
    $('pl-plan-sub').innerHTML = esc(p.group) + (p.combined ? ' · ' + p.plans.length + ' plans as columns, in AV-sequence order' : ' · ' + kindLabel(p.kind))
      + (p.last_synced ? ' · synced ' + esc(fmtWhen(p.last_synced)) : '');
    const open = $('pl-open-planner');
    if (p.url) { open.href = p.url; open.style.display = ''; } else open.style.display = 'none';
    buildFacets();
    writeFilters();
    render();
    wrap.scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  const kindLabel = (k) => ({workflow: 'workflow board', job: 'per-job plan', punch: 'punch list', admin: 'team & admin plan'}[k] || k);

  function buildFacets() {
    const T = S.data.tasks;
    const opt = (sel, values, first) => {
      const cur = sel.value;
      sel.innerHTML = '<option value="">' + first + '</option>' + values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
      sel.value = cur;
    };
    const uniq = (arr) => [...new Set(arr)].sort((a, b) => String(a).localeCompare(String(b)));
    opt(F.division, uniq(T.filter(t => t.project && t.project.division).map(t => t.project.division)), 'all');
    opt(F.assignee, uniq(T.flatMap(t => t.assignees)), 'anyone');
    opt(F.label, uniq(T.flatMap(t => t.labels)), 'any');
  }

  // ---- filtering
  function visible() {
    const f = S.f, q = (f.q || '').toLowerCase(), me = new Set(S.data.me || []);
    return S.data.tasks.filter(t => {
      if (f.state === 'open' && t.percent >= 100) return false;
      if (f.state && f.state !== 'open' && t.state !== f.state) return false;
      if (f.division && !(t.project && t.project.division === f.division)) return false;
      if (f.assignee && !t.assignees.includes(f.assignee)) return false;
      if (f.label && !t.labels.includes(f.label)) return false;
      if (f.overdue && !isOverdue(t)) return false;
      if (f.mine && !t.assignee_ids.some(id => me.has(id))) return false;
      if (f.linked && !t.project) return false;
      if (q) {
        const hay = [t.title, t.bucket, t.plan, t.quote_ref, ...t.labels, ...t.assignees, t.project ? t.project.cpn + ' ' + t.project.title + ' ' + t.project.pm : ''].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  // ---- render
  function render() {
    if (!S.data) return;
    TODAY = todayISO();
    document.querySelectorAll('.pl-view').forEach(b => b.classList.toggle('active', b.dataset.view === S.view));
    $('pl-view-board').hidden = S.view !== 'board'; $('pl-view-list').hidden = S.view !== 'list'; $('pl-view-changes').hidden = S.view !== 'changes';
    const rows = visible();
    const over = rows.filter(isOverdue).length;
    $('pl-counts').textContent = rows.length + ' of ' + S.data.tasks.length + ' tasks' + (over ? ' · ' + over + ' overdue' : '');
    if (S.view === 'board') renderBoard(rows);
    else if (S.view === 'list') renderList(rows);
    else renderChanges();
  }

  function labelChips(t) {
    return t.label_colors.length ? '<div class="pl-labels">' + t.label_colors.map(l => '<span class="pl-label" style="background:' + l.bg + ';color:' + l.fg + '" title="Planner label">' + esc(l.label) + '</span>').join('') + '</div>' : '';
  }
  function dueHtml(t) {
    if (!t.due) return '';
    const od = isOverdue(t), soon = isSoon(t), n = daysTo(t.due);
    const tip = od ? 'Overdue by ' + (-n) + ' day' + (n === -1 ? '' : 's') + ' (due ' + t.due + ')' : t.percent >= 100 ? 'Was due ' + t.due : n === 0 ? 'Due today' : 'Due in ' + n + ' day' + (n === 1 ? '' : 's');
    return '<span class="pl-due' + (od ? ' overdue' : soon ? ' soon' : '') + '" title="' + esc(tip) + '">' + (od ? '⚠ ' : '') + 'Due ' + esc(fmtDate(t.due)) + '</span>';
  }
  function peopleHtml(t) {
    const me = new Set(S.data.me || []);
    if (!t.assignees.length) return '';
    return '<div class="pl-people">' + t.assignees.map((n, i) => '<span class="pl-avatar' + (me.has(t.assignee_ids[i]) ? ' me' : '') + '" title="' + esc(n) + '">' + esc(initials(n)) + '</span>').join('') + '</div>';
  }
  const initials = (n) => { const p = n.trim().split(/[\s,]+/).filter(Boolean); return p.length ? (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase() : '?'; };
  function projectHtml(t) {
    const p = t.project;
    if (!p) return t.quote_ref ? '<div class="pl-quote" title="AV quote number in the title — no SL job with that number yet.">Quote ' + esc(t.quote_ref) + '</div>' : '';
    const figs = [];
    if (S.data.money && p.cv != null) figs.push('<span class="fig" title="SL contract value (current budget basis).">CV ' + esc(money(p.cv)) + '</span>');
    if (S.data.money && p.billed != null) figs.push('<span class="fig" title="Revenue billed to date on the job (SL).">billed ' + esc(money(p.billed)) + '</span>');
    if (p.hours != null) figs.push('<span class="fig" title="PTT hours charged to the job to date' + (p.budget_hours != null ? ' vs ' + Math.round(p.budget_hours).toLocaleString() + ' h budgeted' : '') + '.">' + esc(hours(p.hours)) + (p.budget_hours ? ' / ' + Math.round(p.budget_hours).toLocaleString() : '') + '</span>');
    if (p.pct != null) figs.push('<span class="fig" title="PTT % complete (PM estimate).">' + Math.round(p.pct * 100) + '%</span>');
    const tip = p.number + ' · ' + p.title + (p.customer ? ' · ' + p.customer : '') + (p.pm ? ' · PM ' + p.pm : '') + ' · ' + p.state.replace(/_/g, ' ') + '\nMatched by: ' + (t.match_rule || 'rule');
    return '<div class="pl-proj"><a href="' + CFG.projectBase + encodeURIComponent(p.cpn) + '/" target="_blank" rel="noopener" title="' + esc(tip) + '" onclick="event.stopPropagation()">' + esc(p.number) + '</a>'
      + (p.division ? '<span class="chip" title="Division of the job">' + esc(p.division) + '</span>' : '') + figs.join('') + '</div>';
  }
  function cardHtml(t, sub) {
    const cls = ['pl-card', t.percent >= 100 ? 'done' : '', isOverdue(t) ? 'overdue' : '', t.priority <= 1 ? 'urgent' : t.priority <= 4 ? 'important' : ''].filter(Boolean).join(' ');
    return '<div class="' + cls + '" data-id="' + esc(t.id) + '" title="Click for details · ' + esc(t.priority_label) + ' priority">' + labelChips(t)
      + '<div class="pl-title">' + esc(t.title) + '</div>'
      + '<div class="pl-meta"><span class="pl-state ' + t.state + '" title="' + t.percent + '% complete in Planner">' + esc({not_started: 'Not started', in_progress: 'In progress', complete: 'Complete'}[t.state]) + '</span>'
      + dueHtml(t)
      + (t.checklist_total ? '<span title="Checklist items done / total">☑ ' + t.checklist_done + '/' + t.checklist_total + '</span>' : '')
      + (t.has_description ? '<span title="Has a description">≡</span>' : '') + (t.reference_count ? '<span title="' + t.reference_count + ' attachment(s) / link(s)">⎘ ' + t.reference_count + '</span>' : '')
      + (sub ? '<span class="muted" title="Bucket in its own plan">' + esc(sub) + '</span>' : '')
      + '</div>' + peopleHtml(t) + projectHtml(t) + '</div>';
  }

  function renderBoard(rows) {
    const B = S.data.buckets, byB = {};
    rows.forEach(t => { (byB[t.bucket_id] = byB[t.bucket_id] || []).push(t); });
    const cols = B.map(b => ({b, tasks: byB[b.id] || []}));
    const orphan = rows.filter(t => !B.some(b => b.id === t.bucket_id));
    if (orphan.length) cols.push({b: {id: '', name: 'No bucket'}, tasks: orphan});
    const combined = S.data.plan.combined;
    $('pl-board').innerHTML = cols.map(c => {
      const open = c.tasks.filter(t => t.percent < 100).length, od = c.tasks.filter(isOverdue).length;
      let body;
      if (!c.tasks.length) body = '<div class="pl-col-empty">nothing here' + (S.data.tasks.some(t => t.bucket_id === c.b.id) ? ' after filters' : '') + '</div>';
      else if (combined) {
        const groups = {};
        c.tasks.forEach(t => { (groups[t.bucket] = groups[t.bucket] || []).push(t); });
        body = Object.keys(groups).sort().map(g => '<div class="pl-subhead">' + esc(g || 'no bucket') + ' · ' + groups[g].length + '</div>' + groups[g].map(t => cardHtml(t)).join('')).join('');
      } else body = c.tasks.map(t => cardHtml(t)).join('');
      const head = c.b.plan_url ? '<a href="' + esc(c.b.plan_url) + '" target="_blank" rel="noopener" title="Open this plan in Planner">' + esc(c.b.name) + '</a>' : esc(c.b.name);
      return '<div class="pl-col"><div class="pl-col-head"><span>' + head + '</span><span class="n" title="Open / all cards in this column' + (od ? ' · ' + od + ' overdue' : '') + '">' + open + '/' + c.tasks.length + (od ? ' <span class="neg">⚠' + od + '</span>' : '') + '</span></div><div class="pl-col-body">' + body + '</div></div>';
    }).join('') || '<div class="pl-empty-board">This plan has no buckets yet.</div>';
    $('pl-board').querySelectorAll('.pl-card').forEach(el => el.addEventListener('click', () => openTask(el.dataset.id)));
  }

  // ---- list with multi-key sort
  const COLS = [
    {k: 'title', l: 'Task', tip: 'Task title (click a row for details; the ↗ opens it in Planner)'},
    {k: 'bucket', l: 'Bucket', tip: 'Bucket (column) in Planner'},
    {k: 'labels', l: 'Labels', tip: 'Planner labels'},
    {k: 'state', l: 'State', tip: 'Not started / in progress / complete'},
    {k: 'priority', l: 'Priority', tip: 'Planner priority: urgent (0-1), important (2-4), medium (5-7), low (8-10)'},
    {k: 'start', l: 'Start', tip: 'Start date'},
    {k: 'due', l: 'Due', tip: 'Due date; red = overdue on the live clock'},
    {k: 'assignees', l: 'Assigned', tip: 'Assigned people'},
    {k: 'checklist', l: 'Checklist', tip: 'Checklist items done / total'},
    {k: 'project', l: 'Job', tip: 'Linked SL job (number in the title or bucket, or the quote number)'},
    {k: 'cv', l: 'CV', tip: 'SL contract value of the linked job', money: true},
    {k: 'billed', l: 'Billed', tip: 'Billed to date on the linked job', money: true},
    {k: 'hours', l: 'PTT h', tip: 'PTT hours to date on the linked job'},
    {k: 'last_change', l: 'Changed', tip: 'When PCA last saw a tracked change on the task'},
  ];
  const val = (t, k) => {
    switch (k) {
      case 'labels': return t.labels.join(', ');
      case 'assignees': return t.assignees.join(', ');
      case 'checklist': return t.checklist_total ? t.checklist_done / t.checklist_total : -1;
      case 'project': return t.project ? t.project.number : (t.quote_ref || '');
      case 'cv': return t.project && t.project.cv != null ? t.project.cv : null;
      case 'billed': return t.project && t.project.billed != null ? t.project.billed : null;
      case 'hours': return t.project && t.project.hours != null ? t.project.hours : null;
      case 'state': return {not_started: 0, in_progress: 1, complete: 2}[t.state];
      default: return t[k];
    }
  };
  function cmp(a, b) {
    for (const [k, dir] of S.sort) {
      let x = val(a, k), y = val(b, k);
      const nx = x == null || x === '', ny = y == null || y === '';
      if (nx && ny) continue;
      if (nx) return 1; if (ny) return -1;
      let c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true});
      if (c) return dir === 'desc' ? -c : c;
    }
    return 0;
  }
  function renderList(rows) {
    const cols = COLS.filter(c => !c.money || S.data.money);
    const thead = '<tr>' + cols.map(c => {
      const i = S.sort.findIndex(s => s[0] === c.k);
      return '<th data-k="' + c.k + '" class="' + (i >= 0 ? 'sorted' : '') + '" title="' + esc(c.tip) + '">' + esc(c.l) + (i >= 0 ? '<span class="dir">' + (S.sort[i][1] === 'asc' ? '▲' : '▼') + (S.sort.length > 1 ? i + 1 : '') + '</span>' : '') + '</th>';
    }).join('') + '</tr>';
    const sorted = rows.slice().sort(cmp);
    const tbody = sorted.map(t => '<tr data-id="' + esc(t.id) + '" class="' + (isOverdue(t) ? 'overdue' : '') + '">' + cols.map(c => {
      switch (c.k) {
        case 'title': return '<td class="wrap"><b>' + esc(t.title) + '</b> <a href="' + esc(t.url) + '" target="_blank" rel="noopener" title="Open in Planner" onclick="event.stopPropagation()">↗</a>' + (S.data.plan.combined ? '<div class="muted" style="font-size:11px">' + esc(t.plan) + '</div>' : '') + '</td>';
        case 'labels': return '<td class="wrap">' + t.label_colors.map(l => '<span class="pl-label" style="background:' + l.bg + ';color:' + l.fg + '">' + esc(l.label) + '</span> ').join('') + '</td>';
        case 'state': return '<td><span class="pl-state ' + t.state + '">' + esc({not_started: 'Not started', in_progress: 'In progress', complete: 'Complete'}[t.state]) + '</span></td>';
        case 'priority': return '<td>' + esc(t.priority_label) + '</td>';
        case 'start': return '<td>' + esc(fmtDate(t.start)) + '</td>';
        case 'due': return '<td>' + dueHtml(t) + '</td>';
        case 'assignees': return '<td class="wrap">' + esc(t.assignees.join(', ')) + '</td>';
        case 'checklist': return '<td>' + (t.checklist_total ? t.checklist_done + '/' + t.checklist_total : '') + '</td>';
        case 'project': return '<td>' + (t.project ? '<a href="' + CFG.projectBase + encodeURIComponent(t.project.cpn) + '/" target="_blank" rel="noopener" title="' + esc(t.project.title) + '" onclick="event.stopPropagation()">' + esc(t.project.number) + '</a>' + (t.project.division ? ' <span class="chip">' + esc(t.project.division) + '</span>' : '') : (t.quote_ref ? '<span class="muted">' + esc(t.quote_ref) + '</span>' : '')) + '</td>';
        case 'cv': return '<td>' + (t.project && t.project.cv != null ? esc(money(t.project.cv)) : '') + '</td>';
        case 'billed': return '<td>' + (t.project && t.project.billed != null ? esc(money(t.project.billed)) : '') + '</td>';
        case 'hours': return '<td>' + (t.project && t.project.hours != null ? Math.round(t.project.hours).toLocaleString() : '') + '</td>';
        case 'last_change': return '<td title="' + esc(fmtWhen(t.last_change)) + '">' + esc(t.last_change ? rel(t.last_change).replace('changed ', '') : '') + '</td>';
        default: return '<td>' + esc(t[c.k]) + '</td>';
      }
    }).join('') + '</tr>').join('');
    const tbl = $('pl-list');
    tbl.querySelector('thead').innerHTML = thead;
    tbl.querySelector('tbody').innerHTML = tbody || '<tr><td colspan="' + cols.length + '" class="muted">No tasks match the filters.</td></tr>';
    tbl.querySelectorAll('th').forEach(th => th.addEventListener('click', (e) => {
      const k = th.dataset.k, i = S.sort.findIndex(s => s[0] === k);
      if (e.shiftKey) { if (i >= 0) S.sort[i][1] = S.sort[i][1] === 'asc' ? 'desc' : 'asc'; else S.sort.push([k, 'asc']); }
      else S.sort = [[k, i === 0 && S.sort[0][1] === 'asc' ? 'desc' : 'asc']];
      setPref('sort', S.sort); render();
    }));
    tbl.querySelectorAll('tbody tr[data-id]').forEach(tr => tr.addEventListener('click', () => openTask(tr.dataset.id)));
  }

  // ---- changes
  function renderChanges() {
    const H = S.data.history || [];
    $('pl-changes-count').textContent = H.length + ' change' + (H.length === 1 ? '' : 's') + ' in the last ' + S.data.since + ' days';
    let day = '', html = '';
    H.forEach(h => {
      const d = new Date(h.at).toLocaleDateString('en-US', {timeZone: 'America/Chicago', weekday: 'short', month: 'short', day: 'numeric'});
      if (d !== day) { day = d; html += '<tr class="pl-day"><td colspan="6">' + esc(d) + '</td></tr>'; }
      const time = new Date(h.at).toLocaleTimeString('en-US', {timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit'});
      const what = {created: 'created', deleted: 'deleted', percent: '% complete', bucket: 'moved bucket', due: 'due date', start: 'start date', title: 'renamed', priority: 'priority', labels: 'labels', assignees: 'assigned', completed: 'completed'}[h.field] || h.field;
      html += '<tr data-id="' + esc(h.task_id) + '"><td>' + esc(time) + '</td><td class="wrap"><a href="#" data-open="' + esc(h.task_id) + '">' + esc(h.task_title) + '</a></td><td>' + esc(h.bucket) + '</td><td><span class="pl-field ' + esc(h.field) + '">' + esc(what) + '</span></td><td class="wrap muted">' + esc(h.old) + '</td><td class="wrap">' + esc(h.new) + '</td></tr>';
    });
    $('pl-changes').querySelector('tbody').innerHTML = html || '<tr><td colspan="6" class="muted">No tracked changes in this window. Widen "since".</td></tr>';
    $('pl-changes').querySelectorAll('a[data-open]').forEach(a => a.addEventListener('click', (e) => { e.preventDefault(); openTask(a.dataset.open); }));
  }

  // ---- task modal
  const modal = $('pl-modal');
  function closeModal() { modal.style.display = 'none'; }
  $('pl-modal-close').addEventListener('click', (e) => { e.preventDefault(); closeModal(); });
  modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  function openTask(id) {
    const t = S.data.tasks.find(x => x.id === id);
    if (!t) return;
    const p = t.project;
    const hist = (S.data.history || []).filter(h => h.task_id === id);
    $('pl-modal-title').innerHTML = esc(t.title) + ' <a class="btn small" href="' + esc(t.url) + '" target="_blank" rel="noopener" style="margin-left:8px">Open in Planner ↗</a>';
    $('pl-modal-body').innerHTML = labelChips(t)
      + '<dl class="kv dense">'
      + '<dt>Plan</dt><dd>' + esc(t.group) + ' › ' + esc(t.plan) + (t.bucket ? ' › ' + esc(t.bucket) : '') + '</dd>'
      + '<dt>State</dt><dd><span class="pl-state ' + t.state + '">' + esc({not_started: 'Not started', in_progress: 'In progress', complete: 'Complete'}[t.state]) + '</span> ' + t.percent + '% · ' + esc(t.priority_label) + ' priority' + (t.completed_at ? ' · completed ' + esc(fmtWhen(t.completed_at)) : '') + '</dd>'
      + '<dt>Dates</dt><dd>' + (t.start ? 'start ' + esc(fmtDate(t.start)) + ' · ' : '') + (t.due ? dueHtml(t) : '<span class="muted">no due date</span>') + (t.created ? ' · created ' + esc(fmtWhen(t.created)) : '') + '</dd>'
      + '<dt>Assigned</dt><dd>' + (t.assignees.length ? esc(t.assignees.join(', ')) : '<span class="muted">nobody</span>') + '</dd>'
      + (p ? '<dt>SL job</dt><dd><a href="' + CFG.projectBase + encodeURIComponent(p.cpn) + '/" target="_blank" rel="noopener"><b>' + esc(p.number) + '</b></a> ' + esc(p.title) + (p.customer ? ' · ' + esc(p.customer) : '') + (p.pm ? ' · PM ' + esc(p.pm) : '') + ' · ' + esc(p.state.replace(/_/g, ' ')) + (p.division ? ' <span class="chip">' + esc(p.division) + '</span>' : '')
          + '<div class="muted" style="font-size:12px">' + (S.data.money && p.cv != null ? 'CV ' + esc(money(p.cv)) + ' · ' : '') + (S.data.money && p.billed != null ? 'billed ' + esc(money(p.billed)) + ' · ' : '') + (p.hours != null ? esc(hours(p.hours)) + (p.budget_hours ? ' of ' + Math.round(p.budget_hours).toLocaleString() + ' h budgeted' : '') : '') + (p.pct != null ? ' · PTT ' + Math.round(p.pct * 100) + '% complete' : '') + ' · matched by ' + esc(t.match_rule || 'rule') + '</div></dd>' : (t.quote_ref ? '<dt>Quote</dt><dd>' + esc(t.quote_ref) + ' <span class="muted">(no SL job with that number yet)</span></dd>' : ''))
      + '</dl>'
      + (t.has_description ? (t.description ? '<div class="pl-desc">' + esc(t.description) + '</div>' : '<div class="note" style="margin-top:8px">Description not pulled yet (next refresh).</div>') : '')
      + (t.checklist_total ? '<h3 style="margin-top:10px">Checklist ' + t.checklist_done + '/' + t.checklist_total + '</h3>' + (t.checklist.length ? '<ul class="pl-checklist">' + t.checklist.map(c => '<li class="' + (c.done ? 'done' : '') + '">' + (c.done ? '☑' : '☐') + ' ' + esc(c.title) + '</li>').join('') + '</ul>' : '<div class="note">Items not pulled yet (next refresh).</div>') : '')
      + (t.references.length ? '<h3 style="margin-top:10px">Attachments &amp; links</h3><div class="pl-refs">' + t.references.map(r => '<a href="' + esc(decodeURIComponent(r.url)) + '" target="_blank" rel="noopener">' + esc(r.alias || r.url) + '</a>').join('') + '</div>' : '')
      + '<h3 style="margin-top:10px">Changes seen (last ' + S.data.since + ' days)</h3>'
      + (hist.length ? '<div class="pl-modal-hist"><table class="data dense"><tbody>' + hist.map(h => '<tr><td>' + esc(fmtWhen(h.at)) + '</td><td><span class="pl-field ' + esc(h.field) + '">' + esc(h.field) + '</span></td><td class="muted">' + esc(h.old) + '</td><td>' + esc(h.new) + '</td></tr>').join('') + '</tbody></table></div>' : '<div class="note">none in this window</div>');
    modal.style.display = 'flex';
  }

  // ---- live clock: re-render when the Central date rolls over
  setInterval(() => { const t = todayISO(); if (t !== TODAY) { TODAY = t; render(); } }, 60000);

  // ---- start
  const start = CFG.planParam || pref('plan', '');
  if (start) load(start); else $('pl-picker').open = true;
})();
