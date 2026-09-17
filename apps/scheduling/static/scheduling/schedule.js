// Resource Scheduler page (apps/scheduling). Rendering + interactions only: every number comes from the server payload
// (apps/scheduling/maths.py via services.board_payload) and every edit is a POST to planning_schedule_edit that returns
// the fresh board. PCA.pref remembers every toggle; the week lives in the URL (?week=) so the sidebar restores it.
window.PCASched = (function () {
  const CFG = JSON.parse(document.getElementById('sched-cfg').textContent);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const h = v => { v = +v || 0; return Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10); };
  const parseD = s => { const [y, m, d] = String(s).slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
  const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const mondayOf = d => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const fmtShort = k => parseD(k).toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
  const fmtLong = k => parseD(k).toLocaleDateString('en-US', {weekday: 'long', month: 'short', day: 'numeric'});
  const fmtRange = (a, b) => a === b ? fmtShort(a) : fmtShort(a) + ' – ' + fmtShort(b);
  const csrf = () => (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
  const todayKey = () => iso(new Date());   // live clock (Owner: never frozen at load)
  const natural = (a, b) => String(a).localeCompare(String(b), undefined, {numeric: true, sensitivity: 'base'});
  const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const NONUNION = 'Non-Union';
  const isUnion = t => (t || '') !== NONUNION;

  let B = null, weekKey = CFG.monday;
  const weeks = {};
  const P = {
    view: PCA.pref('view', 'schedule'), div: PCA.pref('div', ''), pm: PCA.pref('pm', ''), rows: PCA.pref('rows', 'all'),
    needsDiv: PCA.pref('needs.div', null), needsTab: PCA.pref('needs.tab', null), cardsDiv: PCA.pref('cards.div', null), cardsPm: PCA.pref('cards.pm', ''),
    ganttDiv: PCA.pref('gantt.div', ''),
  };
  const ui = {needsOpen: new Set(), needsPicked: false, needsTabPicked: false};

  // ------------------------------------------------------------ data
  async function loadWeek(k) {
    const r = await fetch(CFG.urls.json + '?week=' + k, {credentials: 'same-origin'});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'load failed');
    weeks[k] = j.board;
    return j.board;
  }
  async function getWeek(k) { return weeks[k] || loadWeek(k); }
  async function refresh() { B = await loadWeek(weekKey); render(); }
  async function post(action, payload, opts) {
    opts = opts || {};
    const wk = opts.week || weekKey;
    const r = await fetch(CFG.urls.edit, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
                                          body: JSON.stringify(Object.assign({action: action, week: wk}, payload))});
    let j;
    try { j = await r.json(); } catch (e) { j = {ok: false, error: 'Server error (' + r.status + ')'}; }
    if (!j.ok) {
      if (!opts.quiet) toast(j.error || 'Could not save.', true);
      const e = new Error(j.error || 'error'); e.reason = j.reason; e.status = r.status; throw e;
    }
    if (j.board) {
      weeks[wk] = j.board;
      if (wk === weekKey) { B = j.board; render(); } else { await refresh(); }
    }
    return j;
  }
  function toast(msg, err) {
    const t = document.getElementById('sch-toast');
    t.textContent = msg; t.classList.toggle('err', !!err); t.hidden = false;
    clearTimeout(t._tm); t._tm = setTimeout(() => { t.hidden = true; }, 5200);
  }

  // ------------------------------------------------------------ helpers over the payload
  const card = id => B.cards.find(c => c.id === id);
  const laneOf = (W, id) => W.lane.find(l => l.id === id);
  const row = (W, id) => W.rows.find(r => r.id === id);
  const divLabel = d => d ? d : 'No division';
  function filterProject(p) {
    if (P.div && (p.division || '') !== (P.div === 'none' ? '' : P.div)) return false;
    if (P.pm && (p.pm || '') !== (P.pm === 'none' ? '' : P.pm)) return false;
    return true;
  }
  function filterRow(r) {
    if (P.div && P.div !== 'none' && r.divisions.length && !r.divisions.includes(P.div)) return false;
    if (P.rows === 'booked' && !(r.util.used > 0)) return false;
    return true;
  }
  function chipMatches(c) {
    if (P.div && (c.division || '') !== (P.div === 'none' ? '' : P.div)) return false;
    if (P.pm) { const p = card(c.project_id); if (!p || (p.pm || '') !== (P.pm === 'none' ? '' : P.pm)) return false; }
    return true;
  }

  // ------------------------------------------------------------ render
  function render() {
    document.querySelectorAll('#viewToggle button').forEach(b => b.classList.toggle('on', b.dataset.view === P.view));
    document.getElementById('view-schedule').hidden = P.view !== 'schedule';
    document.getElementById('view-projects').hidden = P.view !== 'projects';
    document.getElementById('weekNav').style.visibility = P.view === 'schedule' ? '' : 'hidden';
    document.getElementById('wkLabel').textContent = B.week.label;
    fillFilters();
    if (P.view === 'schedule') { renderSummary(); renderBoard(); renderNeeds(); renderAdherence(); }
    else { renderGantt(); renderCards(); }
    const q = new URLSearchParams(location.search); q.set('week', weekKey);
    history.replaceState(null, '', location.pathname + '?' + q.toString());
    PCA.rememberFilters();
    const base = CFG.urls.export + '?week=' + weekKey;
    document.getElementById('expCsv').href = base + '&kind=week_csv';
    document.getElementById('expXlsx').href = base + '&kind=xlsx';
  }
  function fillFilters() {
    const divs = B.divisions.slice();
    const hasNone = B.cards.some(c => !c.division);
    const setOpts = (sel, opts, cur) => {
      const s = document.getElementById(sel); const keep = cur;
      s.innerHTML = opts.map(o => '<option value="' + esc(o[0]) + '">' + esc(o[1]) + '</option>').join('');
      s.value = keep; if (s.value !== keep) s.value = '';
    };
    setOpts('fDiv', [['', 'All divisions']].concat(divs.map(d => [d, d])).concat(hasNone ? [['none', 'No division']] : []), P.div);
    setOpts('gDiv', [['', 'All divisions · ' + B.gantt.bars.length]].concat(divs.map(d => [d, d + ' · ' + B.gantt.bars.filter(b => b.division === d).length])).concat(hasNone ? [['none', 'No division']] : []), P.ganttDiv);
    const pms = B.pms.map(p => [p.label, p.label]);
    const hasNoPm = B.cards.some(c => !c.pm);
    setOpts('fPm', [['', 'All PMs']].concat(pms).concat(hasNoPm ? [['none', 'No PM']] : []), P.pm);
    setOpts('cPm', [['', 'All PMs']].concat(pms).concat(hasNoPm ? [['none', 'No PM']] : []), P.cardsPm);
    document.getElementById('fRows').value = P.rows;
  }
  function renderSummary() {
    const U = B.summary.union, N = B.summary.nonunion;
    document.getElementById('weekSummary').innerHTML =
      '<span class="ws-big" title="Union weekly capacity (max hrs/week minus 8h per PTO weekday) not booked this week.">' + h(U.free) + 'h</span>' +
      '<span class="ws-lbl">Union hours not utilized this week</span>' +
      '<span class="ws-sub" title="Booked = hours scheduled Mon–Sun, capped at each person\'s weekly capacity.">' + h(U.booked) + 'h of ' + h(U.cap) + 'h scheduled · ' + U.pct + '% utilized</span>' +
      (N.cap ? '<span class="ws-alt" title="Same tally for Non-Union people.">Non-Union: <b>' + h(N.free) + 'h</b> free · ' + N.pct + '% utilized</span>' : '') +
      '<span class="ws-bar" title="Union utilization"><span class="ws-fill" style="width:' + U.pct + '%"></span></span>';
  }
  function tagHtml(t, small) {
    return '<span class="tg tg-' + t.state + '" title="' + esc(t.name) + ': ' + h(t.got) + 'h assigned today of ' + h(t.planned) + 'h planned · ' + (t.state === 'ok' ? 'fully allocated' : t.state === 'part' ? 'partly allocated' : 'nothing assigned yet') + '">' + esc(t.name) + (small ? '' : '') + '</span>';
  }
  function renderBoard() {
    const days = B.week.days, lane = B.lane.filter(filterProject), rows = B.rows.filter(filterRow);
    let html = '<table class="sch-board" style="min-width:' + (200 + days.length * 150) + 'px"><colgroup><col style="width:200px">' + days.map(() => '<col>').join('') + '</colgroup><thead><tr><th class="corner" title="People on the roster shown with the current filters.">Resources · ' + rows.length + '</th>';
    days.forEach(d => {
      html += '<th class="day' + (d.weekend ? ' wknd' : '') + (d.date === todayKey() ? ' today' : '') + '"><div class="dow">' + d.dow + '</div><div class="dnum">' + d.day + '</div>' +
        '<div class="free' + (d.free <= 0 ? ' zero' : '') + '" title="Hours still available that day across everyone not on PTO: for each person min(8h − hours that day, weekly cap left).">' + h(d.free) + 'h free</div></th>';
    });
    html += '</tr></thead><tbody>';
    lane.forEach(p => {
      html += '<tr class="plane"><td class="pname" title="' + esc(p.name) + ' · PM ' + esc(p.pm || '—') + ' · ' + esc(divLabel(p.division)) + '"><span class="dot" style="background:' + p.div_colour + '"></span>' + esc(p.name) + '<div class="sub">' + esc(p.pm || 'No PM') + ' · ' + esc(divLabel(p.division)) + '</div></td>';
      days.forEach(d => {
        const b = p.blocks[d.date];
        html += '<td class="pcell' + (d.weekend ? ' wknd' : '') + '">';
        if (b) {
          html += '<div class="pblock' + (p.completed_early ? ' ce' : '') + '" data-proj="' + p.id + '" data-date="' + d.date + '" style="background:' + p.pm_colour + ';border-left:5px solid ' + p.div_colour + '" title="' + esc(p.name) + ' [' + b.tags.map(t => t.name).join(', ') + '] · ' + esc(p.access) + ' · ' + h(b.assigned) + 'h assigned · click to assign' + (p.completed_early ? ' · completed early' : '') + '">' +
            '<div class="pb-top"><span class="pb-name">' + esc(p.name) + '</span><span class="pb-h">' + h(b.assigned) + 'h</span></div><div class="pb-acc">' + esc(p.access) + '</div><div class="pb-tags">' + b.tags.map(t => tagHtml(t)).join('') + '</div></div>';
        }
        html += '</td>';
      });
      html += '</tr>';
    });
    rows.forEach(r => {
      const u = r.util;
      html += '<tr><td class="rname" data-res="' + r.id + '" title="' + esc(r.name) + ' · ' + esc(r.trade) + (r.divisions.length ? ' · ' + r.divisions.join(' / ') : '') + (r.ot ? ' · Approved Overtime' : '') + ' · click to edit"><div class="nm"><span class="dot"></span>' + esc(r.name) + '</div><div class="sub">' + esc(r.trade) + (r.employee_id ? '' : ' · <span title="No PTT / SL employee is linked to this name — link one in the editor for PTT hours.">unlinked</span>') + '</div>' +
        '<div class="ubar' + (u.over ? ' warn' : u.full ? ' full' : '') + '" title="' + h(u.used) + 'h booked of a ' + h(u.cap) + 'h weekly cap (PTO-adjusted)."><span style="width:' + u.pct + '%"></span></div><div class="uh' + (u.over ? ' warn' : '') + '">' + h(u.used) + '/' + h(u.cap) + 'h</div></td>';
      days.forEach(d => {
        const c = r.cells[d.date];
        if (c.pto) { html += '<td class="cell pto' + (d.weekend ? ' wknd' : '') + '" data-res="' + r.id + '" data-date="' + d.date + '" data-pto="1" title="' + esc(r.name) + ' is on PTO / vacation on ' + fmtShort(d.date) + ' and can\'t be allocated">✕</td>'; return; }
        html += '<td class="cell' + (d.weekend ? ' wknd' : '') + '" data-res="' + r.id + '" data-date="' + d.date + '" title="' + esc(r.name) + ' · ' + fmtShort(d.date) + ' · ' + h(c.room) + 'h room (min of daily 8h and weekly cap left)' + (CFG.can_write ? ' · click to assign' : '') + '">';
        const chips = c.chips.filter(chipMatches);
        if (!chips.length && CFG.can_write) html += '<span class="plus">+</span>';
        chips.forEach(ch => {
          html += '<div class="chip-a' + (ch.completed_early ? ' ce' : '') + '" draggable="' + (CFG.can_write ? 'true' : 'false') + '" data-res="' + r.id + '" data-proj="' + ch.project_id + '" data-date="' + d.date + '" data-first="' + ch.items[0].id + '" style="background:' + ch.pm_colour + ';border-left:4px solid ' + ch.div_colour + '" title="' + esc(ch.name) + ' · ' + h(ch.hours) + 'h · ' + esc(ch.access) + (ch.items.some(i => i.note) ? ' · note: ' + esc(ch.items.filter(i => i.note).map(i => i.note).join('; ')) : '') + ' · click to edit, drag to another day">' +
            '<div class="c-top"><span class="c-name">' + esc(ch.name) + '</span><span class="c-h">' + h(ch.hours) + 'h</span></div>' + (ch.note ? '<span class="c-note"></span>' : '') + '<div class="c-acc">' + esc(ch.access) + '</div>' +
            '<div class="c-tags">' + ch.items.map(i => '<span class="tg" data-asg="' + i.id + '" style="background:' + i.colour + '" title="' + esc(i.phase || 'No phase') + ' ' + h(i.hours) + 'h · click to edit">' + esc(i.phase || 'No phase') + ' ' + h(i.hours) + 'h</span>').join('') + '</div></div>';
        });
        html += '</td>';
      });
      html += '</tr>';
    });
    html += '<tr class="foot"><td class="fname">Daily load</td>' + days.map(d => { const f = B.footer[d.date]; return '<td title="Hours scheduled that day and people with hours.">' + h(f.hours) + 'h · ' + f.heads + ' on duty</td>'; }).join('') + '</tr>';
    html += '</tbody></table>';
    const wrap = document.getElementById('boardWrap');
    const sl = wrap.scrollLeft, st = wrap.scrollTop;
    wrap.innerHTML = html; wrap.scrollLeft = sl; wrap.scrollTop = st;
    document.getElementById('boardHint').textContent = (lane.length ? lane.length + ' project' + (lane.length === 1 ? '' : 's') + ' on site this week' : 'No project has a phase on site this week') + (P.div || P.pm ? ' (filtered)' : '');
    document.getElementById('statLine').innerHTML = '<span>' + B.summary.resources + ' resources</span><span>' + B.summary.projects + ' projects</span><span title="Assignments dated in this week.">' + B.summary.shifts + ' shifts this week</span><span>' + h(B.summary.hours) + 'h scheduled</span>';
    wireBoard(wrap);
  }
  function wireBoard(wrap) {
    wrap.querySelectorAll('.pblock').forEach(el => el.onclick = () => openProjectAssign(+el.dataset.proj, el.dataset.date, 'day'));
    wrap.querySelectorAll('td.rname').forEach(el => el.onclick = () => openPersonModal(+el.dataset.res));
    wrap.querySelectorAll('td.cell').forEach(td => {
      td.onclick = e => {
        if (td.dataset.pto) { toast(td.title); return; }
        if (!CFG.can_write) return;
        const tag = e.target.closest('.tg[data-asg]');
        if (tag) { openAssignmentModal(+td.dataset.res, td.dataset.date, +tag.dataset.asg); return; }
        const chip = e.target.closest('.chip-a');
        if (chip) { openAssignmentModal(+td.dataset.res, td.dataset.date, +chip.dataset.first); return; }
        openAssignmentModal(+td.dataset.res, td.dataset.date, null);
      };
      if (!CFG.can_write || td.dataset.pto) return;
      td.addEventListener('dragover', e => { const d = drag; if (!d || d.res !== +td.dataset.res || d.date === td.dataset.date) return; e.preventDefault(); td.classList.add('dragover'); });
      td.addEventListener('dragleave', () => td.classList.remove('dragover'));
      td.addEventListener('drop', async e => {
        e.preventDefault(); td.classList.remove('dragover');
        const d = drag; drag = null; if (!d || d.res !== +td.dataset.res) return;
        try { await post('assignment_move', {resource_id: d.res, project_id: d.proj, from: d.date, date: td.dataset.date}); toast('Moved to ' + fmtShort(td.dataset.date)); } catch (err) {}
      });
    });
    wrap.querySelectorAll('.chip-a[draggable=true]').forEach(ch => {
      ch.addEventListener('dragstart', e => { drag = {res: +ch.dataset.res, proj: +ch.dataset.proj, date: ch.dataset.date}; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', 'chip'); } catch (x) {} });
      ch.addEventListener('dragend', () => { drag = null; wrap.querySelectorAll('.dragover').forEach(x => x.classList.remove('dragover')); });
    });
  }
  let drag = null;

  // ------------------------------------------------------------ needs rail
  function renderNeeds() {
    const box = document.getElementById('needsBox');
    const items = B.needs.slice().sort((a, b) => natural(a.name, b.name));
    const divs = {};
    items.forEach(n => { const k = n.division || 'Other'; divs[k] = (divs[k] || 0) + 1; });
    const divKeys = Object.keys(divs).sort((a, b) => a === 'Other' ? 1 : b === 'Other' ? -1 : natural(a, b));
    let cur = P.needsDiv;
    if (!cur || !divs[cur]) { cur = divKeys.find(k => divs[k] > 0) || null; if (!ui.needsPicked) P.needsDiv = cur; }
    const inDiv = items.filter(n => (n.division || 'Other') === cur);
    const nRes = inDiv.filter(n => n.kind === 'resources').length, nDates = inDiv.filter(n => n.kind === 'dates').length;
    let tab = P.needsTab;
    if (!tab || (tab === 'resources' && !nRes && nDates) || (tab === 'dates' && !nDates && nRes)) { tab = nRes ? 'resources' : 'dates'; if (!ui.needsTabPicked) P.needsTab = tab; }
    let html = '<h2><span>Needs staffing</span><span class="pill warn" title="Projects that still need people or dates.">' + items.length + '</span></h2>';
    html += '<div class="sch-tabs">' + divKeys.map(k => '<button type="button" data-ndiv="' + esc(k) + '" class="' + (k === cur ? 'on' : '') + '">' + esc(k) + '<span class="n">' + divs[k] + '</span></button>').join('') + '</div>';
    html += '<div class="sch-subtabs"><button type="button" data-ntab="resources" class="' + (tab === 'resources' ? 'on' : '') + '">Needs Resources (' + nRes + ')</button><button type="button" data-ntab="dates" class="' + (tab === 'dates' ? 'on' : '') + '">Needs Dates (' + nDates + ')</button></div>';
    const shown = inDiv.filter(n => n.kind === tab);
    if (!shown.length) html += '<div class="empty" style="padding:14px 4px">Nothing here — every ' + cur + ' project ' + (tab === 'dates' ? 'has dates' : 'is staffed') + '.</div>';
    shown.forEach(n => {
      const g = n.got_need;
      html += '<div class="need-item" data-nid="' + n.id + '"><div class="ni-name" title="' + (tab === 'dates' ? 'Open the project editor' : 'Open the staffing view (all phases)') + '"><span class="dot" style="background:' + n.colour + '"></span>' + esc(n.name) + '</div>';
      if (tab === 'dates') { html += '<div class="ni-flag dates">NEEDS DATES TO SCHEDULE</div>'; }
      else {
        html += '<div class="ni-tot" title="got = hours assigned (by the phase\'s trade, else the person\'s); need = phase hours by trade (else the project totals).">Union ' + h(g.got_u) + '/' + h(g.need_u) + 'h · Non-Union ' + h(g.got_n) + '/' + h(g.need_n) + 'h</div><div class="ni-flag">NEED RESOURCES</div>';
        const open = ui.needsOpen.has(n.id);
        html += ' <span class="ni-caret" data-ncar="' + n.id + '">' + (open ? 'Collapse ▴' : 'Expand ▾') + '</span>';
        if (open) {
          html += '<div class="ni-detail">';
          [['union', 'Union'], ['nonunion', 'Non-Union']].forEach(([k, lbl]) => {
            const t = n.trades[k]; if (!t) return;
            if (t.no_data) { html += '<div class="ni-trade">' + lbl + '</div><div class="ni-plan">Set phase dates to schedule.</div>'; return; }
            html += '<div class="ni-trade' + (t.done ? ' done' : '') + '">' + lbl + ' · ' + h(t.assigned) + '/' + h(t.need) + 'h' + (t.done ? ' · fully staffed' : '') + '</div>';
            t.rows.filter(r => r.rem > 0).forEach(r => {
              html += '<div class="ni-ph"><b>' + esc(r.name) + '</b> ' + h(r.got) + '/' + h(r.total) + 'h' + (r.plan ? '<div class="ni-plan" title="Crew plan for the hours still open: ceil(hours ÷ 8) resource-days, front-loaded.">' + esc(r.plan) + '</div>' : '') +
                r.days_need.slice(0, 12).map(d => '<div class="ni-day">' + fmtShort(d.date) + ' · still needs ' + esc(d.text) + '</div>').join('') + (r.days_need.length > 12 ? '<div class="ni-day muted">' + (r.days_need.length - 12) + ' more days</div>' : '') + '</div>';
            });
          });
          html += '</div>';
        }
      }
      html += '</div>';
    });
    box.innerHTML = html;
    box.querySelectorAll('[data-ndiv]').forEach(b => b.onclick = () => { P.needsDiv = b.dataset.ndiv; ui.needsPicked = true; PCA.setPref('needs.div', P.needsDiv); renderNeeds(); });
    box.querySelectorAll('[data-ntab]').forEach(b => b.onclick = () => { P.needsTab = b.dataset.ntab; ui.needsTabPicked = true; PCA.setPref('needs.tab', P.needsTab); renderNeeds(); });
    box.querySelectorAll('[data-ncar]').forEach(b => b.onclick = () => { const id = +b.dataset.ncar; ui.needsOpen.has(id) ? ui.needsOpen.delete(id) : ui.needsOpen.add(id); renderNeeds(); });
    box.querySelectorAll('.need-item .ni-name').forEach(el => el.onclick = () => {
      const n = items.find(x => x.id === +el.closest('.need-item').dataset.nid);
      if (n.kind === 'dates') openProjectModal(n.id); else openProjectAssign(n.id, n.first_date, 'all');
    });
  }

  // ------------------------------------------------------------ adherence (RS-15)
  function renderAdherence() {
    const rows = B.enrich.adherence;
    const el = document.getElementById('adherence');
    if (!rows.length) { el.innerHTML = '<div class="empty">No comparable rows this week — planned hours need a person with a PTT link on a project with an SL number, and PTT hours need entries dated this week.</div>'; return; }
    const tp = rows.reduce((s, r) => s + r.planned, 0), tw = rows.reduce((s, r) => s + r.worked, 0);
    const lab = {match: 'as planned', under: 'under plan', over: 'over plan', no_time: 'no PTT time', unplanned: 'not planned'};
    el.innerHTML = '<div class="table-wrap tall" style="max-height:300px"><table class="data dense"><thead><tr><th>Person</th><th>Project</th><th class="num" title="Σ assignment hours this week on this board.">Planned</th><th class="num" title="Σ PTT job-report hours this week (form type 1, live entries).">Worked</th><th class="num" title="Worked − planned.">Δ</th><th></th></tr></thead><tbody>' +
      rows.map(r => '<tr><td>' + esc(r.resource) + '</td><td>' + esc(r.project) + '</td><td class="num">' + h(r.planned) + '</td><td class="num">' + h(r.worked) + '</td><td class="num ' + (r.delta > 0 ? 'pos' : r.delta < 0 ? 'neg' : '') + '">' + (r.delta > 0 ? '+' : '') + h(r.delta) + '</td><td><span class="adh-state ' + r.state + '">' + lab[r.state] + '</span></td></tr>').join('') +
      '</tbody><tfoot><tr><td colspan="2">' + rows.length + ' person-project rows</td><td class="num">' + h(tp) + '</td><td class="num">' + h(tw) + '</td><td class="num">' + (tw - tp > 0 ? '+' : '') + h(tw - tp) + '</td><td></td></tr></tfoot></table></div>';
  }

  // ------------------------------------------------------------ projects view
  function renderGantt() {
    const g = B.gantt, px = g.px_per_day;
    const bars = g.bars.filter(b => !P.ganttDiv || (P.ganttDiv === 'none' ? !b.division : b.division === P.ganttDiv)).sort((a, b) => natural(a.name, b.name));
    const start = parseD(g.start), end = parseD(g.end), nDays = Math.round((end - start) / 864e5) + 1, W = nDays * px;
    const x = k => Math.round((parseD(k) - start) / 864e5) * px;
    let ticks = '', lines = '';
    for (let i = 0; i < nDays; i++) {
      const d = addDays(start, i);
      if (d.getDate() === 1) { ticks += '<div class="g-tick month" style="left:' + (i * px) + 'px"><span class="lab">' + d.toLocaleDateString('en-US', {month: 'short'}) + (d.getMonth() === 0 || i === 0 ? ' ' + d.getFullYear() : '') + '</span></div>'; lines += '<div class="g-tick month" style="left:' + (i * px) + 'px"></div>'; }
      else if (d.getDay() === 1) { ticks += '<div class="g-tick" style="left:' + (i * px) + 'px"><span class="wk">' + d.getDate() + '</span></div>'; lines += '<div class="g-tick" style="left:' + (i * px) + 'px"></div>'; }
    }
    const tx = x(todayKey());
    const todayLine = tx >= 0 && tx <= W ? '<div class="g-today" style="left:' + tx + 'px" title="Today"></div>' : '';
    let html = '<div class="g-inner" style="width:' + (W + 260) + 'px"><div class="g-row head"><div class="g-name">Project</div><div class="g-canvas" style="width:' + W + 'px">' + ticks + todayLine + '</div></div>';
    bars.forEach(b => {
      const left = x(b.start), width = Math.max(px, (Math.round((parseD(b.end) - parseD(b.start)) / 864e5) + 1) * px);
      html += '<div class="g-row"><div class="g-name" data-proj="' + b.id + '" title="' + esc(b.name) + ' · ' + fmtRange(b.start, b.end) + ' · ' + b.days + ' days · ' + esc(b.pm || 'No PM') + ' · ' + esc(divLabel(b.division)) + '"><span class="dot" style="background:' + b.div_colour + '"></span>' + esc(b.name) + '<div class="sub">' + esc(b.pm || 'No PM') + ' · ' + esc(divLabel(b.division)) + '</div></div>' +
        '<div class="g-canvas" style="width:' + W + 'px">' + lines + todayLine + '<div class="g-bar' + (b.completed_early ? ' ce' : '') + '" data-proj="' + b.id + '" style="left:' + left + 'px;width:' + width + 'px;background:' + b.pm_colour + ';border-left:4px solid ' + b.div_colour + '" title="' + esc(b.name) + ' · ' + fmtRange(b.start, b.end) + ' · ' + b.days + ' days · ' + esc(b.pm || '') + ' · ' + esc(b.division) + '">' + (width >= 126 ? fmtRange(b.start, b.end) : '') + '</div></div></div>';
    });
    html += '</div>';
    const el = document.getElementById('gantt');
    const first = !el.dataset.ready;
    el.innerHTML = html; el.dataset.ready = '1';
    if (first) el.scrollLeft = Math.max(0, tx - 60);
    el.querySelectorAll('[data-proj]').forEach(n => n.onclick = () => openProjectModal(+n.dataset.proj));
    const dated = bars.length;
    document.getElementById('ganttRange').textContent = fmtShort(g.start) + ' – ' + fmtShort(g.end) + ' · ' + dated + ' project' + (dated === 1 ? '' : 's') + ' with dates · scroll the bar to look ahead';
    document.getElementById('gDiv').value = P.ganttDiv;
  }
  function renderCards() {
    const cards = B.cards.slice().sort((a, b) => natural(a.name, b.name));
    const divs = {};
    cards.forEach(c => { const k = c.division || 'Other'; divs[k] = (divs[k] || 0) + 1; });
    const keys = Object.keys(divs).sort((a, b) => a === 'Other' ? 1 : b === 'Other' ? -1 : natural(a, b));
    let cur = P.cardsDiv;
    if (!cur || !divs[cur]) cur = keys[0] || null;
    document.getElementById('cardTabs').innerHTML = keys.map(k => '<button type="button" data-cdiv="' + esc(k) + '" class="' + (k === cur ? 'on' : '') + '">' + esc(k) + '<span class="n">' + divs[k] + '</span></button>').join('');
    document.querySelectorAll('#cardTabs button').forEach(b => b.onclick = () => { P.cardsDiv = b.dataset.cdiv; PCA.setPref('cards.div', P.cardsDiv); renderCards(); });
    const shown = cards.filter(c => (c.division || 'Other') === cur && (!P.cardsPm || (P.cardsPm === 'none' ? !c.pm : c.pm === P.cardsPm)));
    const el = document.getElementById('cards');
    if (!shown.length) { el.innerHTML = '<div class="empty">No projects here. Click <b>+ Project</b> to add the work you\'re planning over the coming weeks.</div>'; return; }
    el.innerHTML = shown.map(c => {
      const g = c.got_need, days = (c.days || []).map((v, i) => v ? DOW[i] : null).filter(Boolean);
      const dayTxt = days.length === 5 && days[0] === 'Mon' && days[4] === 'Fri' ? 'Mon–Fri' : days.join(' ');
      const enr = B.enrich.projects[String(c.id)];
      const planned = c.phases.reduce((s, p) => s + p.hours, 0);
      return '<div class="pcard" data-proj="' + c.id + '" title="Click to edit dates, hours and phases"><div class="pc-bar" style="background:' + c.colour + '"></div>' +
        '<div><div class="pc-name">' + esc(c.name) + (c.source === 'Project Status' ? ' <span class="src-badge" title="Sent from the status board">status board</span>' : '') + '</div>' +
        '<div class="pc-meta">' + (c.start && c.end ? fmtRange(c.start, c.end) : '<span class="neg">no dates</span>') + ' · ' + dayTxt + ' · ' + c.hours_per_day + 'h/day · ' + h(c.hours_union) + 'h union / ' + h(c.hours_nonunion) + 'h non-union · ' + esc(c.access) + '</div>' +
        '<div class="pc-prog"><div title="Union hours assigned vs needed">Union ' + g.pct_u + '%</div><div class="bar"><span style="width:' + g.pct_u + '%"></span></div>' + (g.need_n ? '<div title="Non-Union hours assigned vs needed">Non-Union ' + g.pct_n + '%</div><div class="bar n"><span style="width:' + g.pct_n + '%"></span></div>' : '') + '</div>' +
        '<div class="pc-phases">' + c.phases.map(p => '<span class="ph-chip ' + (p.staffed ? 'ph-ok' : 'ph-need') + '" title="' + esc(p.name) + ' (' + p.trade + ') · ' + h(p.got) + '/' + h(p.hours) + 'h' + (p.plan ? ' · ' + esc(p.plan) : '') + '">' + esc(p.name) + (p.start ? ' ' + fmtRange(p.start, p.end) + ' · ' + p.days + 'd' : ' · no dates') + '</span>').join('') + '</div></div>' +
        '<div class="pc-right"><div title="Union got / need">Union <b>' + h(g.got_u) + '/' + h(g.need_u) + 'h</b></div><div title="Non-Union got / need">Non-Union <b>' + h(g.got_n) + '/' + h(g.need_n) + 'h</b></div><div>' + g.people + ' ' + (g.people === 1 ? 'person' : 'people') + ' assigned</div>' +
        (c.completed_early ? '<div><span class="ce-badge" title="Completed early (' + esc(c.completed_early.scope) + '): allocations after this date were released">Completed early · ' + fmtShort(c.completed_early.date) + '</span></div>' : '') + (c.fully_staffed ? '<div class="pos" style="font-weight:700">Fully staffed</div>' : '') + '</div>' +
        (enr ? '<div class="pc-enrich"><a href="' + enr.url + '" target="_blank" title="Open the SL project page in a new tab">' + esc(enr.number) + '</a> · PTT worked <b title="Hours PTT has recorded on this job to date (all people)">' + (enr.ptt_hours == null ? '—' : h(enr.ptt_hours) + 'h') + '</b> · PM remaining <b title="The PM\'s current remaining-hours estimate in PTT' + (enr.remaining_at ? ' (' + enr.remaining_at.slice(0, 10) + ')' : '') + '">' + (enr.remaining == null ? '—' : h(enr.remaining) + 'h') + '</b> · SL labor budget <b title="Current SL labor budget hours">' + (enr.budget_hours == null ? '—' : h(enr.budget_hours) + 'h') + '</b> · planned here <b title="Σ phase hours on this board">' + h(planned) + 'h</b>' + (enr.remaining != null && planned > enr.remaining ? ' <span class="neg" title="More hours planned than the PM says remain">plan &gt; remaining</span>' : '') + '</div>' : '<div class="pc-enrich muted">Not linked to an SL project — link it in the editor for PTT / SL hours.</div>') +
        '</div>';
    }).join('');
    el.querySelectorAll('.pcard').forEach(c => c.onclick = e => { if (e.target.closest('a')) return; openProjectModal(+c.dataset.proj); });
  }

  // ------------------------------------------------------------ modal plumbing
  function modal(html, cls) {
    const m = document.getElementById('sch-modal'), box = document.getElementById('sch-modal-box');
    box.className = 'sch-modal-box' + (cls ? ' ' + cls : ''); box.innerHTML = html; m.hidden = false;
    return box;
  }
  function closeModal() { document.getElementById('sch-modal').hidden = true; document.getElementById('sch-modal-box').innerHTML = ''; }
  function dialog(html) { const d = document.getElementById('sch-dialog'), box = document.getElementById('sch-dialog-box'); box.className = 'sch-modal-box'; box.innerHTML = html; d.hidden = false; return box; }
  function closeDialog() { document.getElementById('sch-dialog').hidden = true; }
  document.getElementById('sch-modal').addEventListener('mousedown', e => { if (e.target.id === 'sch-modal') closeModal(); });
  document.getElementById('sch-dialog').addEventListener('mousedown', e => { if (e.target.id === 'sch-dialog') closeDialog(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') { if (!document.getElementById('sch-dialog').hidden) closeDialog(); else closeModal(); } });
  const dateInput = (id, v, extra) => '<input type="date" id="' + id + '" value="' + (v || '') + '" ' + (extra || '') + '>';

  // ------------------------------------------------------------ person-day assignment modal (RS-09)
  async function openAssignmentModal(resId, dk, asgId) {
    const W = await getWeek(iso(mondayOf(parseD(dk))));
    const r = row(W, resId); if (!r) return;
    const cell = r.cells[dk] || {chips: [], room: 0, used: 0};
    const existing = asgId ? cell.chips.flatMap(c => c.items.map(i => Object.assign({project_id: c.project_id}, i))).find(i => i.id === asgId) : null;
    const active = B.cards.filter(c => c.start && c.end && dk >= c.start && dk <= c.end && (c.days || [1, 1, 1, 1, 1, 0, 0])[(parseD(dk).getDay() + 6) % 7]);
    const u = r.util, weekLeft = Math.max(0, u.cap - u.used);
    if (!active.length && !existing) {
      const box = modal('<h2>' + esc(r.name) + ' · ' + fmtLong(dk) + '</h2><div class="msub">No project on site this day</div><div class="empty">No project window (earliest / latest on-site dates and days-needed toggles) covers ' + fmtShort(dk) + '.</div><div class="mfoot"><button class="btn" id="mCancel">Close</button>' + (CFG.can_write ? '<button class="btn primary" id="mNewP">+ New project</button>' : '') + '</div>');
      box.querySelector('#mCancel').onclick = closeModal;
      const np = box.querySelector('#mNewP'); if (np) np.onclick = () => openProjectModal(null, {start: dk});
      return;
    }
    const projOpts = active.map(c => c.id);
    if (existing && !projOpts.includes(existing.project_id)) projOpts.unshift(existing.project_id);
    const box = modal('<h2>' + esc(r.name) + ' · ' + fmtLong(dk) + '</h2><div class="msub">' + esc(r.trade) + (r.ot ? ' · Approved Overtime (caps bypassed)' : '') + '</div>' +
      '<div class="cap-hint" title="Weekly cap = max hrs/week minus 8h per PTO weekday.">This week: <b>' + h(u.used) + 'h</b> used of <b>' + h(u.cap) + 'h</b> · <b>' + h(weekLeft) + 'h</b> left · today <b>' + h(cell.room) + 'h</b> room (8h daily cap)</div>' +
      '<div class="sch-form"><label class="full">Project<select id="aProj">' + projOpts.map(id => { const c = card(id); return '<option value="' + id + '">' + esc(c.name) + ' (' + c.hours_per_day + 'h/day)</option>'; }).join('') + '</select></label>' +
      '<label class="full">Phase<select id="aPhase"></select></label><div class="full muted" id="aAccess" style="font-size:12px"></div>' +
      '<label>Hours<input type="number" id="aHours" min="0" max="24" step="0.5" value="' + (existing ? existing.hours : Math.min(8, Math.max(0, cell.room)) || 8) + '"></label>' +
      '<label>Note<input type="text" id="aNote" maxlength="200" value="' + esc(existing ? existing.note : '') + '" placeholder="optional"></label></div><div class="sch-err" id="aErr"></div>' +
      '<div class="mfoot">' + (existing ? '<button class="btn left" id="aDel" style="color:var(--bad)">Delete</button>' : '') + '<button class="btn" id="aCancel">Cancel</button><button class="btn primary" id="aSave">Save</button></div>');
    const projSel = box.querySelector('#aProj'), phSel = box.querySelector('#aPhase');
    if (existing) projSel.value = existing.project_id;
    const fillPhases = () => {
      const pid = +projSel.value, ln = laneOf(W, pid), tags = ln && ln.blocks[dk] ? ln.blocks[dk].tags : [];
      const c = card(pid);
      const mine = tags.filter(t => { const ph = c.phases.find(p => p.id === t.id); return ph && (isUnion(r.trade) ? ph.trade === 'Union' : ph.trade === NONUNION); });
      phSel.innerHTML = '<option value="">No phase</option>' + mine.map(t => '<option value="' + t.id + '">' + esc(t.name) + '</option>').join('');
      if (existing && existing.phase_id) phSel.value = existing.phase_id;
      box.querySelector('#aAccess').textContent = 'Site access: ' + c.access;
    };
    projSel.onchange = fillPhases; fillPhases();
    box.querySelector('#aCancel').onclick = closeModal;
    box.querySelector('#aSave').onclick = async () => {
      try {
        await post('assignment_save', {id: existing ? existing.id : null, resource_id: resId, project_id: +projSel.value, date: dk, hours: +box.querySelector('#aHours').value, phase_id: phSel.value || null, note: box.querySelector('#aNote').value}, {quiet: true});
        closeModal();
      } catch (e) { box.querySelector('#aErr').textContent = e.message; }
    };
    const del = box.querySelector('#aDel'); if (del) del.onclick = async () => { await post('assignment_delete', {id: existing.id}); closeModal(); };
    setTimeout(() => box.querySelector('#aHours').focus(), 40);
  }

  // ------------------------------------------------------------ project staffing modal (RS-10)
  const ms = {tab: 'assign', q: '', role: 'Union', div: null, copySel: {}, panel: null};
  async function openProjectAssign(pid, dk, scope) {
    const c = card(pid); if (!c) return;
    const W = await getWeek(iso(mondayOf(parseD(dk))));
    const ln = laneOf(W, pid), dayTags = ln && ln.blocks[dk] ? ln.blocks[pid ? dk : dk].tags : [];
    const phasesAll = c.phases, phasesDay = phasesAll.filter(p => dayTags.some(t => t.id === p.id));
    const sumPhases = scope === 'all' ? phasesAll : phasesDay;
    const byPhaseDates = {};
    c.assigned.forEach(a => { const k = a.phase_id == null ? 'none' : a.phase_id; (byPhaseDates[k] = byPhaseDates[k] || {})[a.date] = (byPhaseDates[k][a.date] || 0) + 1; });
    const shift = c.hours_per_day || 8;
    const grp = (lbl, list) => {
      if (!list.length) return '';
      return '<div class="grp"><div class="grp-t">' + lbl + '</div>' + list.map(ph => {
        const tag = dayTags.find(t => t.id === ph.id);
        const st = ph.staffed ? 'ok' : (tag && tag.state === 'part') || (ph.got > 0) ? 'part' : 'need';
        const dates = Object.keys(byPhaseDates[ph.id] || {}).sort();
        const sel = ms.copySel[ph.id] || new Set();
        const opt = tag ? ' · today ' + h(tag.got) + '/' + h(tag.planned) + 'h planned (' + Math.ceil(tag.planned / shift) + ' resource' + (Math.ceil(tag.planned / shift) === 1 ? '' : 's') + ')' : '';
        return '<div class="pa-ph"><span class="st ' + st + '"></span><span class="nm">' + esc(ph.name) + '</span><span class="muted">' + h(ph.got) + '/' + h(ph.hours) + 'h' + (ph.staffed ? ' · resources required allocated' : '') + opt + '</span>' +
          (ph.plan && !ph.staffed ? '<span class="plan" title="Crew plan for the hours still open">' + esc(ph.plan) + '</span>' : '') +
          (dates.length ? '<span class="alloc"><span class="lbl">Allocated</span>' + dates.map(d => '<span class="dchip' + (sel.has(d) ? ' sel' : '') + (d === dk ? ' today' : '') + '" data-cp="' + ph.id + '" data-cpk="' + d + '" title="' + byPhaseDates[ph.id][d] + ' on ' + fmtShort(d) + ' · click to select as a copy target">' + fmtShort(d) + ' ×' + byPhaseDates[ph.id][d] + '</span>').join('') +
            (CFG.can_write && sel.size && (byPhaseDates[ph.id] || {})[dk] ? '<button type="button" class="btn small" data-cpph="' + ph.id + '" title="Copy today\'s people on this phase to the selected days (replacing what is there; PTO / elsewhere / weekly-cap conflicts are skipped and listed first)">Copy allocation → ' + sel.size + ' day' + (sel.size === 1 ? '' : 's') + '</button>' : '') + '</span>' : '') + '</div>';
      }).join('') + '</div>';
    };
    const remH = sumPhases.reduce((s, p) => s + (p.days ? Math.max(0, p.hours - Math.min(p.got, p.hours)) : 0), 0), needH = sumPhases.reduce((s, p) => s + (p.days ? p.hours : 0), 0);
    const note = !needH ? '' : remH <= 0 ? '<div class="daysum-note ok">Fully staffed — all phase hours met.</div>' : '<div class="daysum-note"><b>' + h(remH) + 'h</b> still open (~<b>' + Math.ceil(remH / shift) + '</b> resource-days). Follow each phase\'s crew plan above and assign whichever days work best.</div>';
    const met = (c.met_days || []).length ? '<div class="daysmet"><span class="lbl">Allocation met</span>' + c.met_days.map(d => '<span class="mchip' + (d === dk ? ' today' : '') + '" title="Every phase active that day is fully staffed or has its crew for the day">' + fmtShort(d) + '</span>').join('') + '</div>' : '';
    const summary = sumPhases.length ? '<div class="pa-sum"><div class="grp-t" style="margin-bottom:4px">' + (scope === 'all' ? 'All phases' : 'Phases on ' + fmtShort(dk)) + '</div>' + grp('Union', sumPhases.filter(p => p.trade === 'Union')) + grp('Non-Union', sumPhases.filter(p => p.trade === NONUNION)) + note + met + '</div>'
      : '<div class="pa-sum empty">' + (scope === 'all' ? 'No phases added to this project yet.' : 'No phases scheduled on ' + fmtShort(dk) + '.') + '</div>';
    const assignedN = new Set(c.assigned.map(a => a.resource_id)).size;
    const box = modal('<div class="mhead"><div><h2><span class="dot" style="background:' + c.colour + '"></span>' + esc(c.name) + '</h2><div class="msub">' + (scope === 'all' ? 'Project overview · all assigned resources' : fmtLong(dk) + ' · ' + esc(c.access)) + '</div></div>' +
      (CFG.can_write ? '<button type="button" class="ce-btn' + (c.completed_early ? ' on' : '') + '" id="paCe" title="Owner the project (or one phase / trade) complete on a date: later allocations are released, the plan totals stay.">' + (c.completed_early ? 'Completed early · ' + fmtShort(c.completed_early.date) : 'Completed Early') + '</button>' : '') + '</div>' +
      summary + '<div class="pa-tabs"><button type="button" data-t="assign" class="' + (ms.tab === 'assign' ? 'on' : '') + '">Assign resources</button><button type="button" data-t="assigned" class="' + (ms.tab === 'assigned' ? 'on' : '') + '">Assigned (' + assignedN + ')</button></div><div id="paBody"></div>' +
      '<div class="mfoot">' + (CFG.can_write ? '<button class="btn left" id="paEdit">Edit dates &amp; hours</button>' : '') + '<button class="btn primary" id="paDone">Done</button></div>', 'wide');
    box.querySelectorAll('[data-t]').forEach(b => b.onclick = () => { ms.tab = b.dataset.t; openProjectAssign(pid, dk, scope); });
    box.querySelector('#paDone').onclick = closeModal;
    const ed = box.querySelector('#paEdit'); if (ed) ed.onclick = () => openProjectModal(pid);
    const ce = box.querySelector('#paCe'); if (ce) ce.onclick = () => openCompletedEarly(pid, dk, scope);
    box.querySelectorAll('[data-cp]').forEach(ch => ch.onclick = () => { const k = +ch.dataset.cp, s = ms.copySel[k] = ms.copySel[k] || new Set(); s.has(ch.dataset.cpk) ? s.delete(ch.dataset.cpk) : s.add(ch.dataset.cpk); openProjectAssign(pid, dk, scope); });
    box.querySelectorAll('[data-cpph]').forEach(b => b.onclick = () => copyAllocation(pid, +b.dataset.cpph, dk, [...ms.copySel[+b.dataset.cpph]], () => openProjectAssign(pid, dk, scope)));
    const body = box.querySelector('#paBody');
    if (ms.tab === 'assigned') renderAssignedTab(body, c, () => openProjectAssign(pid, dk, scope));
    else renderAssignTab(body, c, W, dk, dayTags, () => openProjectAssign(pid, dk, scope));
  }
  function renderAssignedTab(body, c, reopen) {
    const byRes = {};
    c.assigned.forEach(a => (byRes[a.resource_id] = byRes[a.resource_id] || []).push(a));
    const ids = Object.keys(byRes).map(Number).sort((a, b) => natural((row(B, a) || {}).name || '', (row(B, b) || {}).name || ''));
    if (!ids.length) { body.innerHTML = '<div class="empty">Nobody is assigned to this project yet.</div>'; return; }
    body.innerHTML = ids.map(id => { const r = row(B, id), list = byRes[id].slice().sort((a, b) => a.date < b.date ? -1 : 1), tot = list.reduce((s, a) => s + a.hours, 0);
      return '<div class="as-row"><span class="nm">' + esc(r ? r.name : '#' + id) + '</span>' + list.map(a => '<span class="dc">' + fmtShort(a.date) + ' · ' + h(a.hours) + 'h' + (a.phase_name ? ' · ' + esc(a.phase_name) : '') + (CFG.can_write ? '<span class="x" data-del="' + a.id + '" title="Remove">×</span>' : '') + '</span>').join('') + '<span class="tot">' + new Set(list.map(a => a.date)).size + ' day' + (new Set(list.map(a => a.date)).size === 1 ? '' : 's') + ' · ' + h(tot) + 'h</span></div>'; }).join('');
    body.querySelectorAll('[data-del]').forEach(x => x.onclick = async () => { await post('assignment_delete', {id: +x.dataset.del}); reopen(); });
  }
  function renderAssignTab(body, c, W, dk, dayTags, reopen) {
    const projDiv = c.division || '';
    const roles = CFG.trades;
    const all = W.rows.filter(r => r.trade === ms.role && (!ms.q || r.name.toLowerCase().includes(ms.q.toLowerCase())));
    const divKeys = [...new Set([projDiv].concat(B.divisions).filter(Boolean))];
    let divHtml = '';
    let list = all;
    if (ms.role !== NONUNION && divKeys.length) {
      if (ms.div == null || !divKeys.includes(ms.div)) ms.div = projDiv || divKeys[0];
      divHtml = '<div class="sch-tabs" style="margin:0">' + divKeys.map(d => '<button type="button" data-pdiv="' + d + '" class="' + (d === ms.div ? 'on' : '') + '" title="' + (d === projDiv ? 'The project\'s own division' : 'Borrow from ' + d) + '">' + (d === projDiv ? '★ ' : '') + d + '<span class="n">' + all.filter(r => !r.divisions.length || r.divisions.includes(d)).length + '</span></button>').join('') + '</div>';
      list = all.filter(r => !r.divisions.length || r.divisions.includes(ms.div));
    }
    list.sort((a, b) => natural(a.name, b.name));
    let html = '<div class="pa-ctl"><input type="search" id="paQ" placeholder="Search people…" value="' + esc(ms.q) + '"><div class="sch-tabs" style="margin:0">' + roles.map(t => '<button type="button" data-prole="' + esc(t) + '" class="' + (t === ms.role ? 'on' : '') + '">' + esc(t) + '</button>').join('') + '</div>' + divHtml + '</div>';
    html += '<div class="pa-list">';
    if (!list.length) html += '<div class="empty">No ' + esc(ms.div || ms.role) + ' resources match. Use another division tab to borrow someone.</div>';
    const worked = B.enrich.worked;
    list.forEach(r => {
      const cell = r.cells[dk] || {chips: [], room: 0, used: 0, pto: false};
      const mine = cell.chips.find(ch => ch.project_id === c.id), elsewhere = cell.chips.filter(ch => ch.project_id !== c.id);
      const onThis = !!mine, elsewhereN = elsewhere.length;
      const state = onThis && !elsewhereN ? 'good' : elsewhereN ? (cell.room <= 0 ? 'alloc' : 'part') : '';
      const stTxt = state === 'good' ? 'Scheduled' : elsewhereN ? 'Allocated' : '';
      const myPh = c.phases.filter(p => dayTags.some(t => t.id === p.id) && (isUnion(r.trade) ? p.trade === 'Union' : p.trade === NONUNION));
      const opts = Array.from({length: r.ot ? CFG.ot_daily : CFG.daily_cap}, (_, i) => i + 1);
      const dflt = Math.max(1, Math.min(cell.room || 0, 8)) || 8;
      const wk = worked[String(r.id)] && worked[String(r.id)][String(c.id)];
      html += '<div class="pa-row' + (cell.pto ? ' pto' : '') + '" data-res="' + r.id + '"><div class="nm">' + esc(r.name) + (stTxt ? '<span class="st ' + state + '">' + stTxt + '</span>' : '') + (r.divisions.length ? '<span class="divs">' + r.divisions.join(' / ') + '</span>' : (ms.role !== NONUNION ? '<span class="divs" title="No home division set — shows under every tab">set division</span>' : '')) + (wk ? '<span class="worked" title="Hours PTT has recorded for this person on this job">PTT ' + h(wk) + 'h on this job</span>' : '') + '</div>' +
        '<div class="booked" title="Hours booked this week / weekly cap">' + h(r.util.used) + '/' + h(r.util.cap) + 'h booked</div>' +
        (cell.pto ? '<div class="left zero">On PTO</div><div class="act muted" style="font-size:11px">cannot be allocated</div>' :
          '<div class="left' + (cell.room <= 0 && !r.ot ? ' zero' : '') + '" title="min(weekly cap left, 8h − hours today)' + (r.ot ? ' · Approved Overtime: 12h/day' : '') + '">' + h(cell.room) + 'h left</div>' +
          (CFG.can_write ? '<div class="act"><label class="ot" title="Approved Overtime — ignore hour caps for this person"><input type="checkbox" data-ot="' + r.id + '"' + (r.ot ? ' checked' : '') + '>OT</label><select data-hrs="' + r.id + '">' + opts.map(o => '<option value="' + o + '"' + (o === dflt ? ' selected' : '') + '>' + o + 'h</option>').join('') + '</select>' +
            (myPh.length ? '<select data-ph="' + r.id + '">' + myPh.map(p => '<option value="' + p.id + '">' + esc(p.name) + '</option>').join('') + '</select><button type="button" class="btn small primary" data-assign="' + r.id + '">' + (mine ? 'Assign / Edit phases' : 'Assign') + '</button>' : '<button type="button" class="btn small primary" data-assign="' + r.id + '" data-nophase="1">Assign</button>') + '</div>' : '<div></div>')) +
        (elsewhereN ? '<div class="where">' + elsewhere.map(ch => esc(ch.name) + ch.items.map(i => ' · ' + esc(i.phase || 'no phase') + ' ' + h(i.hours) + 'h').join('')).join(' ; ') + '</div>' : '') +
        (mine ? '<div class="mine">' + mine.items.map(i => '<span class="it" title="Change hours (0 removes)">' + esc(i.phase || 'No phase') + (CFG.can_write ? ' <select data-edit="' + i.id + '">' + [0].concat(opts).map(o => '<option value="' + o + '"' + (o === i.hours ? ' selected' : '') + '>' + o + 'h</option>').join('') + '</select><span class="x" data-rm="' + i.id + '" title="Remove">×</span>' : ' ' + h(i.hours) + 'h') + '</span>').join('') + '</div>' : '') +
        (ms.panel === r.id && myPh.length ? '<div class="pa-panel" data-panel="' + r.id + '"><b style="font-size:12px">Which phases today?</b>' + myPh.map(p => { const it = mine && mine.items.find(i => i.phase_id === p.id); return '<label><input type="checkbox" data-pp="' + p.id + '"' + (it ? ' checked' : '') + '> ' + esc(p.name) + ' <input type="number" min="0.5" max="24" step="0.5" data-pph="' + p.id + '" value="' + (it ? it.hours : dflt) + '">h</label>'; }).join('') + '<button type="button" class="btn small primary" data-psave="' + r.id + '">Save</button><button type="button" class="btn small" data-pcancel="1">Cancel</button></div>' : '') +
        '</div>';
    });
    html += '</div>';
    body.innerHTML = html;
    const q = body.querySelector('#paQ');
    q.oninput = () => { ms.q = q.value; const pos = q.selectionStart; reopen(); const nq = document.getElementById('paQ'); if (nq) { nq.focus(); nq.setSelectionRange(pos, pos); } };
    body.querySelectorAll('[data-prole]').forEach(b => b.onclick = () => { ms.role = b.dataset.prole; reopen(); });
    body.querySelectorAll('[data-pdiv]').forEach(b => b.onclick = () => { ms.div = b.dataset.pdiv; reopen(); });
    body.querySelectorAll('[data-ot]').forEach(cb => cb.onchange = async () => { await post('resource_ot', {id: +cb.dataset.ot, ot: cb.checked}, {week: W.week.monday}); reopen(); });
    body.querySelectorAll('[data-hrs]').forEach(s => s.addEventListener('keydown', e => { if (e.key === 'Enter') { const b = body.querySelector('[data-assign="' + s.dataset.hrs + '"]'); if (b) b.click(); } }));
    body.querySelectorAll('[data-assign]').forEach(b => b.onclick = async () => {
      const rid = +b.dataset.assign, rowEl = b.closest('.pa-row');
      const mine = (row(W, rid).cells[dk] || {chips: []}).chips.find(ch => ch.project_id === c.id);
      if (!b.dataset.nophase && mine) { ms.panel = rid; reopen(); return; }
      const phSel = rowEl.querySelector('[data-ph]');
      try { await post('assignment_save', {resource_id: rid, project_id: c.id, date: dk, hours: +rowEl.querySelector('[data-hrs]').value, phase_id: phSel ? +phSel.value : null}, {week: W.week.monday}); reopen(); }
      catch (e) { if (e.reason === 'daily' || e.reason === 'weekly') capWarn(e.reason, e.message); }
    });
    body.querySelectorAll('[data-edit]').forEach(s => s.onchange = async () => {
      try { await post('assignment_save', {id: +s.dataset.edit, hours: +s.value}, {week: W.week.monday}); reopen(); }
      catch (e) { if (e.reason === 'daily' || e.reason === 'weekly') capWarn(e.reason, e.message); reopen(); }
    });
    body.querySelectorAll('[data-rm]').forEach(x => x.onclick = async () => { await post('assignment_delete', {id: +x.dataset.rm}, {week: W.week.monday}); reopen(); });
    body.querySelectorAll('[data-pcancel]').forEach(x => x.onclick = () => { ms.panel = null; reopen(); });
    body.querySelectorAll('[data-psave]').forEach(b => b.onclick = async () => {
      const rid = +b.dataset.psave, panel = body.querySelector('[data-panel="' + rid + '"]');
      const items = [...panel.querySelectorAll('[data-pp]')].filter(cb => cb.checked).map(cb => ({phase_id: +cb.dataset.pp, hours: +panel.querySelector('[data-pph="' + cb.dataset.pp + '"]').value}));
      try { await post('phase_panel', {resource_id: rid, project_id: c.id, date: dk, items: items}, {week: W.week.monday}); ms.panel = null; reopen(); }
      catch (e) { if (e.reason === 'daily' || e.reason === 'weekly') capWarn(e.reason, e.message); }
    });
  }
  function capWarn(kind, msg) {
    const box = dialog('<h2>' + (kind === 'daily' ? 'Daily hours exceeded' : 'Weekly hours exceeded') + '</h2><div class="msub">' + (kind === 'daily' ? 'This assignment has surpassed this resource\'s daily hours.' : 'This assignment has surpassed this resource\'s weekly hours.') + '</div><div>' + esc(msg) + '</div><div class="mfoot"><button class="btn primary" id="cwOk">OK</button></div>');
    box.querySelector('#cwOk').onclick = closeDialog;
  }
  async function copyAllocation(pid, phaseId, src, dates, reopen) {
    const prev = await post('copy_preview', {project_id: pid, phase_id: phaseId, source_date: src, dates: dates});
    const go = async () => { const r = await post('copy_apply', {project_id: pid, phase_id: phaseId, source_date: src, dates: dates}); ms.copySel[phaseId] = new Set(); toast('Copied ' + r.copied + ' allocation' + (r.copied === 1 ? '' : 's') + (r.skipped ? ' · ' + r.skipped + ' skipped' : '')); reopen(); };
    if (!prev.conflicts.length) { await go(); return; }
    const n = prev.conflicts.length;
    const box = dialog('<h2>Can\'t copy to every day</h2><div class="msub">' + n + ' allocation' + (n === 1 ? '' : 's') + ' can\'t be copied. Everything else will still be copied.</div>' +
      prev.conflicts.map(cf => '<div class="cc-item"><div class="who">' + esc(cf.name) + '<span class="day">' + fmtShort(cf.date) + '</span></div><div class="reason">' + esc(cf.reason) + '</div>' + (cf.where.length ? '<div class="where">' + cf.where.map(w => '<b>' + esc(w.project) + '</b>' + (w.phase ? ' · ' + esc(w.phase) : '') + (w.hours ? ' · ' + h(w.hours) + 'h' : '')).join(' ; ') + '</div>' : '') + '</div>').join('') +
      '<div class="mfoot"><button class="btn" id="ccCancel">Cancel</button><button class="btn primary" id="ccGo">Copy the rest</button></div>');
    box.querySelector('#ccCancel').onclick = closeDialog;
    box.querySelector('#ccGo').onclick = async () => { closeDialog(); await go(); };
  }

  // ------------------------------------------------------------ completed early (RS-11)
  async function openCompletedEarly(pid, dk, scope) {
    const c = card(pid);
    const cur = c.completed_early;
    const pv = await post('ce_preview', {project_id: pid, date: (cur && cur.date) || dk, scope: (cur && cur.scope) || 'all'});
    const opts = pv.options;
    const box = dialog('<h2>Completed early · ' + esc(c.name) + '</h2><div class="msub">Allocations after the completion date are released for the chosen scope; days on or before it stay as worked time and the project totals are unchanged.</div>' +
      '<div class="sch-form"><label>Date project was completed<input type="date" id="ceDate" value="' + ((cur && cur.date) || dk) + '"' + (c.start ? ' min="' + c.start + '"' : '') + '></label>' +
      (opts.length > 1 ? '<label>Which part was completed?<select id="ceScope"><option value="">— choose —</option>' + opts.map(o => '<option value="' + esc(o.value) + '"' + (cur && cur.scope === o.value ? ' selected' : '') + '>' + esc(o.label) + '</option>').join('') + '</select></label>' : '<input type="hidden" id="ceScope" value="' + (opts[0] ? esc(opts[0].value) : 'all') + '">') + '</div>' +
      '<div class="ce-note" id="ceNote"></div><div class="mfoot">' + (cur ? '<button class="btn left" id="ceClear">Not complete</button>' : '') + '<button class="btn" id="ceCancel">Cancel</button><button class="btn primary" id="ceOk">Owner completed</button></div>');
    const dateEl = box.querySelector('#ceDate'), scopeEl = box.querySelector('#ceScope'), note = box.querySelector('#ceNote');
    const upd = async () => {
      const sc = scopeEl.value; if (!dateEl.value || !sc) { note.innerHTML = 'Pick the date' + (opts.length > 1 ? ' and the part that was completed' : '') + '.'; return; }
      const r = await post('ce_preview', {project_id: pid, date: dateEl.value, scope: sc});
      const lbl = (opts.find(o => o.value === sc) || {}).label || sc;
      note.innerHTML = r.count ? '<b>' + r.count + ' allocation' + (r.count === 1 ? '' : 's') + '</b> for "' + esc(lbl) + '" after ' + fmtShort(dateEl.value) + ' will be cleared from the calendar — ' + h(r.hours) + 'h across ' + r.people + ' ' + (r.people === 1 ? 'person' : 'people') + '. Days on or before that date stay as worked time, and the project totals are unchanged.' : 'No allocations after ' + fmtShort(dateEl.value) + ' for "' + esc(lbl) + '" — nothing is cleared; the phase(s) are marked complete.';
    };
    dateEl.onchange = upd; scopeEl.onchange = upd; upd();
    box.querySelector('#ceCancel').onclick = closeDialog;
    box.querySelector('#ceOk').onclick = async () => {
      if (!dateEl.value || !scopeEl.value) return;
      try { await post('completed_early', {project_id: pid, date: dateEl.value, scope: scopeEl.value}); closeDialog(); closeModal(); toast('Marked completed early'); } catch (e) {}
    };
    const cl = box.querySelector('#ceClear'); if (cl) cl.onclick = async () => { await post('completed_early_clear', {project_id: pid}); closeDialog(); closeModal(); };
  }

  // ------------------------------------------------------------ resource editor (RS-01)
  async function openPersonModal(resId) {
    const r = resId ? row(B, resId) : null;
    if (resId && !r) return;
    const divs = [...new Set(['040', '070', '080'].concat(B.divisions))].sort();
    const box = modal('<h2>' + (r ? esc(r.name) : 'New resource') + '</h2><div class="msub">' + (r ? (r.employee_id ? 'Linked to PTT / SL employee #' + r.employee_id : 'Not linked to a PTT / SL employee') : 'The roster is seeded from PTT / SL people; add someone by hand only when they are not an employee (a subcontractor, a temp).') + '</div>' +
      '<div class="sch-form">' + (r ? '' : '<label class="full">Link an employee (optional)<input type="search" id="rEmpQ" placeholder="Type a name to search employees…" autocomplete="off"><div id="rEmpRes" class="muted" style="font-size:12px"></div><input type="hidden" id="rEmpId"></label>') +
      '<label class="full">Name<input type="text" id="rName" value="' + esc(r ? r.name : '') + '"></label>' +
      '<label>Role<select id="rTrade">' + CFG.trades.map(t => '<option' + (r && r.trade === t ? ' selected' : '') + '>' + t + '</option>').join('') + '</select></label>' +
      '<label>Max hrs / week<input type="number" id="rMax" min="0" max="168" value="' + (r ? r.max_weekly : 40) + '"></label>' +
      '<div class="fld full">Divisions<div class="daytog" id="rDivs">' + divs.map(d => '<button type="button" data-d="' + d + '" class="' + (r && r.divisions.includes(d) ? 'on' : '') + '" title="Home division (★ in the assign list)">' + d + '</button>').join('') + '</div></div>' +
      '<label class="chk full"><input type="checkbox" id="rOt"' + (r && r.ot ? ' checked' : '') + '> Approved Overtime — ignore hour caps (12h/day picker)</label>' +
      '<label class="chk full"><input type="checkbox" id="rPtoOn"' + (r && r.pto.length ? ' checked' : '') + '> PTO / Vacation</label>' +
      '<div class="full" id="rPto" ' + (r && r.pto.length ? '' : 'hidden') + '><div id="rPtoRows"></div><span class="addrng" id="rPtoAdd">+ Add dates</span></div>' +
      (r ? '<label class="chk full"><input type="checkbox" id="rActive" checked> Active (untick to hide from the board without deleting)</label>' : '') + '</div><div class="sch-err" id="rErr"></div>' +
      '<div class="mfoot">' + (r && CFG.can_write ? '<button class="btn left" id="rDel" style="color:var(--bad)">Remove</button>' : '') + '<button class="btn" id="rCancel">Cancel</button>' + (CFG.can_write ? '<button class="btn primary" id="rSave">Save</button>' : '') + '</div>');
    const ptoRows = box.querySelector('#rPtoRows');
    const addPto = (s, e) => { const d = document.createElement('div'); d.className = 'pa-ctl'; d.innerHTML = '<input type="date" class="ps" value="' + (s || '') + '"> to <input type="date" class="pe" value="' + (e || '') + '"><span class="x muted" style="cursor:pointer" title="Remove">×</span>'; d.querySelector('.x').onclick = () => d.remove(); d.querySelector('.ps').onchange = () => { if (!d.querySelector('.pe').value) d.querySelector('.pe').value = d.querySelector('.ps').value; }; ptoRows.appendChild(d); };
    (r ? r.pto : []).forEach(p => addPto(p[0], p[1]));
    box.querySelector('#rPtoAdd').onclick = () => addPto('', '');
    box.querySelector('#rPtoOn').onchange = e => { box.querySelector('#rPto').hidden = !e.target.checked; if (e.target.checked && !ptoRows.children.length) addPto('', ''); };
    box.querySelectorAll('#rDivs button').forEach(b => b.onclick = () => b.classList.toggle('on'));
    const eq = box.querySelector('#rEmpQ');
    if (eq) { let tm; eq.oninput = () => { clearTimeout(tm); tm = setTimeout(async () => { const j = await (await fetch(CFG.urls.json + '?kind=employee_search&q=' + encodeURIComponent(eq.value))).json(); box.querySelector('#rEmpRes').innerHTML = j.results.map(e => '<a href="#" data-emp="' + e.id + '" data-name="' + esc(e.name) + '" data-trade="' + esc(e.trade) + '" data-div="' + esc(e.division) + '"' + (e.on_roster ? ' class="muted" title="already on the roster"' : '') + '>' + esc(e.name) + ' <span class="muted">' + esc(e.key) + (e.on_roster ? ' · on roster' : '') + '</span></a>').join(' · ') || '<span class="muted">no employee found</span>'; box.querySelectorAll('[data-emp]').forEach(a => a.onclick = ev => { ev.preventDefault(); box.querySelector('#rEmpId').value = a.dataset.emp; box.querySelector('#rName').value = a.dataset.name; box.querySelector('#rTrade').value = a.dataset.trade; box.querySelectorAll('#rDivs button').forEach(b => b.classList.toggle('on', b.dataset.d === a.dataset.div)); box.querySelector('#rEmpRes').innerHTML = 'Linked: ' + esc(a.dataset.name); }); }, 250); }; }
    box.querySelector('#rCancel').onclick = closeModal;
    const save = async () => {
      const payload = {id: resId, version: r ? r.version : null, display_name: box.querySelector('#rName').value, trade: box.querySelector('#rTrade').value, max_weekly: +box.querySelector('#rMax').value,
        divisions: [...box.querySelectorAll('#rDivs button.on')].map(b => b.dataset.d), approved_ot: box.querySelector('#rOt').checked,
        pto: box.querySelector('#rPtoOn').checked ? [...ptoRows.children].map(d => ({start: d.querySelector('.ps').value, end: d.querySelector('.pe').value})).filter(x => x.start) : [],
        active: r ? box.querySelector('#rActive').checked : true, employee_id: box.querySelector('#rEmpId') ? (box.querySelector('#rEmpId').value || null) : null};
      try { await post('resource_save', payload, {quiet: true}); closeModal(); toast('Saved ' + payload.display_name); }
      catch (e) { box.querySelector('#rErr').textContent = e.message; if (e.status === 409) box.querySelector('#rErr').innerHTML += ' <a href="#" onclick="location.reload();return false">Reload</a>'; }
    };
    const sv = box.querySelector('#rSave'); if (sv) sv.onclick = save;
    box.querySelector('#rName').addEventListener('keydown', e => { if (e.key === 'Enter' && sv) save(); });
    const del = box.querySelector('#rDel'); if (del) del.onclick = async () => { const n = r.util.used; if (!confirm('Remove ' + r.name + ' and all their assignments?')) return; await post('resource_delete', {id: resId}); closeModal(); toast('Removed ' + r.name); };
    setTimeout(() => box.querySelector('#rName').focus(), 40);
  }

  // ------------------------------------------------------------ project editor (RS-02, RS-12)
  async function openProjectModal(pid, preset) {
    let p = null;
    if (pid) { const j = await (await fetch(CFG.urls.json + '?kind=project&id=' + pid)).json(); if (!j.ok) { toast(j.error, true); return; } p = j.project; }
    preset = preset || {};
    const days = p ? p.days : [1, 1, 1, 1, 1, 0, 0];
    const wkOpts = (v) => [['none', 'Weekdays only'], ['sat', '+ include Saturdays'], ['sun', '+ include Sundays'], ['both', '+ include Sat & Sun']].map(o => '<option value="' + o[0] + '"' + (o[0] === (v || 'none') ? ' selected' : '') + '>' + o[1] + '</option>').join('');
    const divs = [...new Set(['040', '070', '080'].concat(B.divisions).concat(p && p.division ? [p.division] : []))].sort();
    const pmOpts = '<option value="">— none —</option>' + CFG.pm_choices.map(o => '<option value="' + o.id + '"' + (p && p.pm_id === o.id ? ' selected' : '') + '>' + esc(o.label) + ' · ' + esc(o.name) + '</option>').join('');
    const phaseRows = CFG.phases.map(name => {
      const mine = p ? p.phases.filter(ph => ph.name === name && !ph.is_short) : [];
      const on = mine.length > 0;
      const tr = CFG.phase_type[name];
      const ranges = mine.flatMap(ph => ph.ranges.length ? ph.ranges.map(r => Object.assign({trade: ph.trade}, r)) : [{start: ph.start, end: ph.end, weekend: ph.weekend, hours: ph.hours, trade: ph.trade}]);
      if (!ranges.length) ranges.push({start: '', end: '', weekend: 'none', hours: '', trade: tr});
      return '<div class="ph-row' + (on ? '' : ' off') + '" data-phase="' + name + '"><input type="checkbox" class="ph-on"' + (on ? ' checked' : '') + '><span class="ph-lab">' + name + '</span><span class="badge ' + (name === 'Test' ? 'b' : tr === 'Union' ? 'u' : 'n') + '">' + (name === 'Test' ? 'Union / Non' : tr) + '</span>' +
        '<div class="ranges">' + ranges.map((r, i) => rangeHtml(name, r, i)).join('') + '<span class="addrng" data-add="' + name + '">+ Other date</span></div><div class="plan"></div></div>';
    }).join('');
    function rangeHtml(name, r, i) {
      return '<div class="rng"><input type="date" class="rs" value="' + (r.start || '') + '"><span class="muted">to</span><input type="date" class="re" value="' + (r.end || '') + '"><select class="rw">' + wkOpts(r.weekend) + '</select><input type="number" class="hrs" min="0" step="1" placeholder="hours" title="Total man-hours for this ' + (i ? 'date range' : 'phase') + '" value="' + (r.hours == null ? '' : r.hours) + '">' + (name === 'Test' ? '<select class="rt"><option' + (r.trade !== NONUNION ? ' selected' : '') + '>Union</option><option' + (r.trade === NONUNION ? ' selected' : '') + '>Non-Union</option></select>' : '') + (i ? '<span class="x" title="Remove this range">×</span>' : '') + '</div>';
    }
    const shortBlock = (key, lbl, trade) => {
      const ph = p ? p.phases.find(x => x.is_short && x.trade === trade) : null;
      const ranges = ph ? (ph.ranges.length ? ph.ranges : [{start: ph.start, end: ph.end, weekend: ph.weekend}]) : [{start: preset.start || '', end: preset.start || '', weekend: 'none'}];
      return '<div class="strade" data-short="' + key + '"><h4>' + lbl + ' <span class="muted" data-sh="' + key + '"></span></h4><div class="ranges">' + ranges.map((r, i) => '<div class="rng"><input type="date" class="rs" value="' + (r.start || '') + '"><span class="muted">to</span><input type="date" class="re" value="' + (r.end || '') + '"><select class="rw">' + wkOpts(r.weekend) + '</select>' + (i ? '<span class="x">×</span>' : '') + '</div>').join('') + '<span class="addrng" data-addshort="' + key + '">+ Other date</span></div><div class="plan muted" style="font-size:11.5px"></div></div>';
    };
    const box = modal('<div class="mhead"><div><h2>' + (p ? esc(p.name) : 'New project') + '</h2><div class="msub">' + (p ? (p.source === 'Project Status' ? 'Sent from the status board' + (p.unscheduled ? ' · unscheduled until it has dates' : '') + ' · editing here takes it out of planner management' : 'Edit dates, hours and phases') : 'Name the job, set its on-site window and the phases with their man-hours; the assistant on the right shows who is free.') + '</div></div>' + (p && p.completed_early ? '<span class="ce-badge">Completed early · ' + fmtShort(p.completed_early.date) + '</span>' : '') + '</div>' +
      '<div class="pe-grid"><div><div class="sch-form">' +
      '<label class="full">Project name<input type="text" id="pName" value="' + esc(p ? p.name : '') + '" placeholder="260078 RUSH DAY SCHOOL …"></label>' +
      '<label class="full">SL project (optional link)<input type="search" id="pProjQ" placeholder="Search by number or title…" value="' + esc(p ? p.project_number : '') + '" autocomplete="off"><div id="pProjRes" class="muted" style="font-size:12px"></div><input type="hidden" id="pProjNum" value="' + esc(p ? p.project_number : '') + '"></label>' +
      '<label>PM<select id="pPm">' + pmOpts + '</select></label><label>PM (as typed, when not in the list)<input type="text" id="pPmRaw" value="' + esc(p ? p.pm_raw : '') + '" placeholder="PATTON"></label>' +
      '<label>Division<select id="pDiv"><option value="">— none —</option>' + divs.map(d => '<option' + (p && p.division === d ? ' selected' : '') + '>' + d + '</option>').join('') + '</select></label>' +
      '<label>Colour<input type="color" id="pColour" value="' + (p && p.colour ? p.colour : '#152dc3') + '"></label>' +
      '<label>Earliest on-site date<input type="date" id="pStart" value="' + (p ? p.start || '' : preset.start || '') + '"></label><label>Latest allowed date<input type="date" id="pEnd" value="' + (p ? p.end || '' : preset.start || '') + '"></label>' +
      '<label>Site access from<input type="time" id="pAs" value="' + (p ? p.access_start : '07:00') + '"></label><label>Site access to<input type="time" id="pAe" value="' + (p ? p.access_end : '15:00') + '"></label>' +
      '<div class="fld full">Days needed on site<div class="daytog" id="pDays">' + DOW.map((d, i) => '<button type="button" data-i="' + i + '" class="' + (days[i] ? 'on' : '') + '">' + d + '</button>').join('') + '</div></div>' +
      '<label>Total Union hours<input type="number" id="pHu" min="0" step="1" value="' + (p ? p.hours_union : 0) + '"></label><label>Total Non-Union hours<input type="number" id="pHn" min="0" step="1" value="' + (p ? p.hours_nonunion : 0) + '"></label>' +
      '<label class="chk full"><input type="checkbox" id="pShort"' + (p && p.short_project ? ' checked' : '') + '> Short Project — track total Union / Non-Union hours over set days instead of phases</label>' +
      '<label class="full">Notes<textarea id="pNotes" rows="2">' + esc(p ? p.notes : '') + '</textarea></label></div>' +
      '<div id="pShortBlocks" ' + (p && p.short_project ? '' : 'hidden') + '>' + shortBlock('short_union', 'Union days', 'Union') + shortBlock('short_nonunion', 'Non-Union days', NONUNION) + '</div>' +
      '<div id="pPhases" class="sch-sect" ' + (p && p.short_project ? 'hidden' : '') + '><h3>Project phases</h3>' + phaseRows + '</div></div>' +
      '<div><div class="av-box" id="avBox"><div class="av-title">Scheduling assistant</div><div class="av-sub">Who is free for the dates you entered — within each person\'s weekly cap.</div><div id="avBody" class="muted">Enter dates and hours to see who is free.</div></div></div></div>' +
      '<div class="sch-err" id="pErr"></div><div class="mfoot">' + (p && CFG.can_write ? '<button class="btn left" id="pDel" style="color:var(--bad)">Delete</button>' : '') + '<button class="btn" id="pCancel">Cancel</button>' + (CFG.can_write ? '<button class="btn primary" id="pSave">Save</button>' : '') + '</div>', 'xwide');
    const $ = s => box.querySelector(s);
    const start = $('#pStart'), end = $('#pEnd');
    const markEmpty = () => { start.classList.toggle('date-empty', !start.value); end.classList.toggle('date-empty', !end.value); box.querySelectorAll('.rng input[type=date]').forEach(i => { i.min = start.value || ''; i.max = end.value || ''; }); };
    start.onchange = () => { if (end.value && end.value < start.value) end.value = start.value; markEmpty(); assist(); };
    end.onchange = () => { if (start.value && end.value < start.value) start.value = end.value; markEmpty(); assist(); };
    markEmpty();
    box.querySelectorAll('#pDays button').forEach(b => b.onclick = () => b.classList.toggle('on'));
    $('#pShort').onchange = () => { $('#pShortBlocks').hidden = !$('#pShort').checked; $('#pPhases').hidden = $('#pShort').checked; assist(); };
    const wireRow = rowEl => {
      rowEl.querySelectorAll('input, select').forEach(i => i.addEventListener('change', assist));
      rowEl.querySelectorAll('.rng .x').forEach(x => x.onclick = () => { x.closest('.rng').remove(); assist(); });
      rowEl.querySelectorAll('.rs').forEach(i => i.addEventListener('change', () => { const re = i.closest('.rng').querySelector('.re'); if (!re.value || re.value < i.value) re.value = i.value; }));
    };
    box.querySelectorAll('.ph-row').forEach(rowEl => {
      const cb = rowEl.querySelector('.ph-on'); cb.onchange = () => { rowEl.classList.toggle('off', !cb.checked); assist(); };
      wireRow(rowEl);
      rowEl.querySelector('[data-add]').onclick = () => { const name = rowEl.dataset.phase, div = document.createElement('div'); div.innerHTML = rangeHtml(name, {start: '', end: '', weekend: 'none', hours: '', trade: CFG.phase_type[name]}, 1); const el = div.firstChild; rowEl.querySelector('.ranges').insertBefore(el, rowEl.querySelector('[data-add]')); wireRow(el); markEmpty(); };
    });
    box.querySelectorAll('.strade').forEach(st => { wireRow(st); st.querySelector('[data-addshort]').onclick = () => { const div = document.createElement('div'); div.innerHTML = '<div class="rng"><input type="date" class="rs"><span class="muted">to</span><input type="date" class="re"><select class="rw">' + wkOpts('none') + '</select><span class="x">×</span></div>'; const el = div.firstChild; st.querySelector('.ranges').insertBefore(el, st.querySelector('[data-addshort]')); wireRow(el); markEmpty(); }; });
    $('#pHu').addEventListener('input', assist); $('#pHn').addEventListener('input', assist);
    const pq = $('#pProjQ'); let ptm;
    pq.oninput = () => { clearTimeout(ptm); ptm = setTimeout(async () => { const j = await (await fetch(CFG.urls.json + '?kind=project_search&q=' + encodeURIComponent(pq.value))).json(); $('#pProjRes').innerHTML = j.results.map(r => '<a href="#" data-pn="' + esc(r.number) + '" data-t="' + esc(r.title) + '" data-d="' + esc(r.division) + '" data-pm="' + (r.pm_id || '') + '" data-bh="' + (r.budget_hours == null ? '' : r.budget_hours) + '" data-rem="' + (r.remaining == null ? '' : r.remaining) + '">' + esc(r.number) + ' ' + esc(r.title) + '</a> <span class="muted">' + esc(r.division) + (r.budget_hours != null ? ' · budget ' + h(r.budget_hours) + 'h' : '') + (r.remaining != null ? ' · PM remaining ' + h(r.remaining) + 'h' : '') + '</span>').join('<br>') || '<span class="muted">nothing found</span>';
      $('#pProjRes').querySelectorAll('[data-pn]').forEach(a => a.onclick = ev => { ev.preventDefault(); $('#pProjNum').value = a.dataset.pn.replace(/-000000$/, ''); pq.value = a.dataset.pn; if (!$('#pName').value) $('#pName').value = a.dataset.pn.replace(/-000000$/, '') + ' ' + a.dataset.t; if (a.dataset.d && !$('#pDiv').value) $('#pDiv').value = a.dataset.d; if (a.dataset.pm && !$('#pPm').value) $('#pPm').value = a.dataset.pm; $('#pProjRes').innerHTML = 'Linked to SL project ' + esc(a.dataset.pn) + (a.dataset.bh ? ' · SL labor budget ' + h(a.dataset.bh) + 'h' : '') + (a.dataset.rem ? ' · PM remaining ' + h(a.dataset.rem) + 'h' : ''); }); }, 250); };
    function collect() {
      const phases = [...box.querySelectorAll('.ph-row')].map(rowEl => ({name: rowEl.dataset.phase, enabled: rowEl.querySelector('.ph-on').checked,
        ranges: [...rowEl.querySelectorAll('.rng')].map(r => ({start: r.querySelector('.rs').value, end: r.querySelector('.re').value, weekend: r.querySelector('.rw').value, hours: +r.querySelector('.hrs').value || 0, trade: r.querySelector('.rt') ? r.querySelector('.rt').value : null}))}));
      const short = key => ({ranges: [...box.querySelector('[data-short="' + key + '"]').querySelectorAll('.rng')].map(r => ({start: r.querySelector('.rs').value, end: r.querySelector('.re').value, weekend: r.querySelector('.rw').value}))});
      return {id: pid, version: p ? p.version : null, name: $('#pName').value, project_number: $('#pProjNum').value, pm_id: $('#pPm').value || null, pm_raw: $('#pPmRaw').value, division: $('#pDiv').value,
        colour: $('#pColour').value, start: start.value, end: end.value, access_start: $('#pAs').value, access_end: $('#pAe').value, days: [...box.querySelectorAll('#pDays button')].map(b => b.classList.contains('on') ? 1 : 0),
        hours_union: +$('#pHu').value || 0, hours_nonunion: +$('#pHn').value || 0, short_project: $('#pShort').checked, notes: $('#pNotes').value, phases: phases, short_union: short('short_union'), short_nonunion: short('short_nonunion')};
    }
    let atm;
    function assist() { clearTimeout(atm); atm = setTimeout(assistNow, 350); }
    async function assistNow() {
      const f = collect(), groups = [];
      if (f.short_project) {
        if (f.hours_union > 0) groups.push({key: 'short_union', label: 'Union days', trade: 'Union', hours: f.hours_union, ranges: f.short_union.ranges});
        if (f.hours_nonunion > 0) groups.push({key: 'short_nonunion', label: 'Non-Union days', trade: NONUNION, hours: f.hours_nonunion, ranges: f.short_nonunion.ranges});
      } else {
        f.phases.filter(ph => ph.enabled).forEach(ph => {
          const byTrade = {};
          ph.ranges.filter(r => r.start).forEach(r => { const t = ph.name === 'Test' ? (r.trade || 'Union') : CFG.phase_type[ph.name]; (byTrade[t] = byTrade[t] || {hours: 0, ranges: []}); byTrade[t].hours += r.hours; byTrade[t].ranges.push(r); });
          Object.keys(byTrade).forEach(t => groups.push({key: ph.name + (ph.name === 'Test' ? '|' + t : ''), label: ph.name + (ph.name === 'Test' ? ' (' + t + ')' : ''), trade: t, hours: byTrade[t].hours, ranges: byTrade[t].ranges}));
        });
      }
      box.querySelectorAll('.ph-row .plan, .strade .plan').forEach(el => el.textContent = '');
      if (!groups.length) { $('#avBody').innerHTML = '<span class="muted">Enter dates and hours to see who is free.</span>'; return; }
      let res;
      try { res = await post('assist', {project_id: pid, groups: groups}, {quiet: true}); } catch (e) { $('#avBody').innerHTML = '<span class="neg">' + esc(e.message) + '</span>'; return; }
      res.groups.forEach(g => {
        const key = g.key.split('|')[0];
        const el = f.short_project ? box.querySelector('[data-short="' + g.key + '"] .plan') : box.querySelector('.ph-row[data-phase="' + key + '"] .plan');
        if (el && g.plan) el.textContent = (el.textContent ? el.textContent + ' · ' : '') + (g.label.includes('(') ? g.label.slice(g.label.indexOf('(')) + ' ' : '') + h(g.hours) + 'h → ' + g.plan;
      });
      $('#avBody').innerHTML = res.groups.map(g => '<div class="av-grp"><div class="g-h"><span>' + esc(g.label) + ' <span class="muted">· ' + h(g.hours) + 'h · ' + g.days + ' day' + (g.days === 1 ? '' : 's') + '</span></span><span class="verdict ' + (g.covered ? 'ok' : g.days ? 'short' : 'na') + '">' + esc(g.verdict) + '</span></div>' +
        '<div class="muted" style="font-size:11.5px">' + h(g.free) + 'h available across ' + g.people.length + ' ' + esc(g.trade) + ' ' + (g.people.length === 1 ? 'person' : 'people') + '</div>' +
        (g.people.length ? '<details><summary>Show names ▸</summary><div class="names">' + g.people.map(pp => esc(pp.name) + ' · ' + h(pp.free) + 'h').join('<br>') + '</div></details>' : '') +
        '<div class="chips">' + g.chips.map(ch => '<span class="dchip ' + ch.state + '" title="' + h(ch.free) + 'h free on ' + fmtShort(ch.date) + '">' + fmtShort(ch.date) + ' ' + h(ch.free) + 'h</span>').join('') + '</div></div>').join('') +
        (res.total ? '<div class="av-grp"><div class="g-h"><span>Project total</span></div>' + Object.keys(res.total.trades).map(t => '<div style="font-size:12px">' + t + ': ' + h(res.total.trades[t].hours) + 'h needed · ' + h(res.total.trades[t].free) + 'h free · <b class="' + (res.total.trades[t].gap > 0 ? 'neg' : 'pos') + '">' + (res.total.trades[t].gap > 0 ? 'short ' + h(res.total.trades[t].gap) + 'h' : 'covered') + '</b></div>').join('') + (res.total.note ? '<div class="av-note">' + esc(res.total.note) + '</div>' : '') + '</div>' : '');
    }
    assist();
    $('#pCancel').onclick = closeModal;
    const sv = $('#pSave');
    if (sv) sv.onclick = async () => {
      const f = collect();
      try {
        const r = await post('project_save', f, {quiet: true, week: f.start && !pid ? iso(mondayOf(parseD(f.start))) : weekKey});
        closeModal(); toast('Saved ' + f.name);
        if (!pid) {   // a new project: jump to its start week, select its division in the rail and the cards
          P.view = 'schedule'; PCA.setPref('view', 'schedule');
          if (f.division) { P.needsDiv = f.division; P.cardsDiv = f.division; ui.needsPicked = true; PCA.setPref('needs.div', f.division); PCA.setPref('cards.div', f.division); }
          if (f.start) { weekKey = iso(mondayOf(parseD(f.start))); B = weeks[weekKey] || await loadWeek(weekKey); }
          render();
        }
      } catch (e) { $('#pErr').textContent = e.message; if (e.status === 409) $('#pErr').innerHTML += ' <a href="#" onclick="location.reload();return false">Reload</a>'; }
    };
    const del = $('#pDel');
    if (del) del.onclick = async () => { const c = card(pid); const n = c ? c.assigned.length : 0; if (!confirm('Delete "' + p.name + '" and its ' + n + ' assignment(s)?')) return; await post('project_delete', {id: pid}); closeModal(); toast('Deleted ' + p.name); };
    setTimeout(() => $('#pName').focus(), 40);
  }

  // ------------------------------------------------------------ export resource schedules (RS-13)
  function openExportResources() {
    const rows = B.rows.slice().sort((a, b) => natural(a.name, b.name));
    const box = modal('<h2>Export resource schedules</h2><div class="msub">Pick the people; opens a standalone weekly schedule for ' + esc(B.week.label) + ' you can print or save.</div>' +
      '<div class="pa-ctl"><input type="search" id="exQ" placeholder="Search…"><label class="chk"><input type="checkbox" id="exAll" checked> Select all</label><span class="muted" id="exN"></span></div>' +
      '<div class="exp-list" id="exList">' + rows.map(r => '<label data-n="' + esc(r.name.toLowerCase()) + '"><input type="checkbox" value="' + r.id + '" checked> ' + esc(r.name) + (r.util.used ? ' <span class="muted">' + h(r.util.used) + 'h</span>' : '') + '</label>').join('') + '</div>' +
      '<div class="mfoot"><button class="btn" id="exCancel">Cancel</button><button class="btn primary" id="exGo">Export</button></div>');
    const cnt = () => { box.querySelector('#exN').textContent = box.querySelectorAll('#exList input:checked').length + ' selected'; };
    box.querySelector('#exQ').oninput = e => { const q = e.target.value.toLowerCase(); box.querySelectorAll('#exList label').forEach(l => l.hidden = q && !l.dataset.n.includes(q)); };
    box.querySelector('#exAll').onchange = e => { box.querySelectorAll('#exList input').forEach(i => { if (!i.closest('label').hidden) i.checked = e.target.checked; }); cnt(); };
    box.querySelectorAll('#exList input').forEach(i => i.onchange = cnt); cnt();
    box.querySelector('#exCancel').onclick = closeModal;
    box.querySelector('#exGo').onclick = () => { const ids = [...box.querySelectorAll('#exList input:checked')].map(i => i.value); window.open(CFG.urls.export + '?kind=resources_html&week=' + weekKey + '&resources=' + ids.join(','), '_blank'); closeModal(); };
  }

  // ------------------------------------------------------------ wiring
  document.querySelectorAll('#viewToggle button').forEach(b => b.onclick = () => { P.view = b.dataset.view; PCA.setPref('view', P.view); render(); });
  document.getElementById('wkPrev').onclick = async () => { weekKey = B.week.prev; B = await getWeek(weekKey); render(); };
  document.getElementById('wkNext').onclick = async () => { weekKey = B.week.next; B = await getWeek(weekKey); render(); };
  document.getElementById('wkToday').onclick = async () => { weekKey = iso(mondayOf(new Date())); B = await loadWeek(weekKey); render(); };
  document.getElementById('fDiv').onchange = e => { P.div = e.target.value; PCA.setPref('div', P.div); render(); };
  document.getElementById('fPm').onchange = e => { P.pm = e.target.value; PCA.setPref('pm', P.pm); render(); };
  document.getElementById('fRows').onchange = e => { P.rows = e.target.value; PCA.setPref('rows', P.rows); render(); };
  document.getElementById('gDiv').onchange = e => { P.ganttDiv = e.target.value; PCA.setPref('gantt.div', P.ganttDiv); renderGantt(); };
  document.getElementById('cPm').onchange = e => { P.cardsPm = e.target.value; PCA.setPref('cards.pm', P.cardsPm); renderCards(); };
  const np = document.getElementById('btnNewProject'); if (np) np.onclick = () => openProjectModal(null);
  const nr = document.getElementById('btnNewResource'); if (nr) nr.onclick = () => openPersonModal(null);
  document.getElementById('expRes').onclick = e => { e.preventDefault(); document.querySelector('.sch-menu').open = false; openExportResources(); };
  document.addEventListener('click', e => { const m = document.querySelector('.sch-menu'); if (m && m.open && !m.contains(e.target)) m.open = false; });

  (async function init() {
    try { B = await loadWeek(weekKey); render(); }
    catch (e) { document.getElementById('boardWrap').innerHTML = '<div class="empty">Could not load the schedule: ' + esc(e.message) + '</div>'; }
  })();

  return {refresh, openProjectAssign, openProjectModal, openPersonModal, openAssignmentModal, state: () => B};
})();
