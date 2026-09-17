/* Shared helpers for the Production pages (apps/planning): CSRF-aware JSON posts, toasts, the live clock, the priority
   and punch rules mirrored from apps/planning/rules.py, status-pill colours, PM colours, in-page confirm. */
window.PL = (function () {
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const tok = () => (document.querySelector('#pl-csrf [name=csrfmiddlewaretoken]') || {}).value || '';
  async function post(url, body, isForm) {
    const opts = {method: 'POST', headers: {'X-CSRFToken': tok()}, credentials: 'same-origin'};
    if (isForm) { body.append('csrfmiddlewaretoken', tok()); opts.body = body; }
    else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body || {}); }
    const r = await fetch(url, opts);
    let data = {};
    try { data = await r.json(); } catch (e) { data = {error: r.status === 403 ? 'Not allowed (' + r.status + ')' : 'Server error ' + r.status}; }
    if (!r.ok && !data.error) data.error = 'Request failed (' + r.status + ')';
    data.__status = r.status;
    return data;
  }
  let toastEl = null, toastT = null;
  function toast(msg, err) {
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'pl-toast'; document.body.appendChild(toastEl); }
    toastEl.textContent = msg; toastEl.classList.toggle('err', !!err); toastEl.classList.add('show');
    clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('show'), err ? 5200 : 3200);
  }
  // ---- live clock: every "today" moves with it (US Central = the browser's local zone in the office)
  const pad = n => (n < 10 ? '0' : '') + n;
  function todayISO() { const d = new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function clock(el) {
    if (!el) return;
    const tick = () => { const d = new Date(); el.textContent = d.toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'}) + ' · ' + d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'}); };
    tick(); setInterval(tick, 15000);
  }
  function parseISO(s) { if (!s) return null; const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s); return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null; }
  function daysUntil(iso, today) { const d = parseISO(iso); if (!d) return null; const t = parseISO(today || todayISO()); return Math.round((d - t) / 86400000); }
  function priority(endIso, today) { const n = daysUntil(endIso, today); if (n == null) return 'none'; if (n < 0) return 'red'; if (n <= 14) return 'yellow'; return 'green'; }
  function startingSoon(startIso, today) { const n = daysUntil(startIso, today); return n != null && n >= 0 && n <= 14; }
  const PRIORITY_LABELS = {red: 'Past critical stop', yellow: 'Due within 14 days', green: 'On track', none: 'No stop date'};
  const PRIORITY_COLORS = {red: '#c62828', yellow: '#b7791f', green: '#178a52', none: '#8a94a3'};
  function punchStatus(it, today) { if (it.date_completed) return 'completed'; const n = daysUntil(it.due_by, today); return (n != null && n < 0) ? 'overdue' : 'open'; }
  function critOpen(it) { return !it.date_completed && (+it.critical === 4 || +it.critical === 5); }
  function punchStats(items, today) {
    const s = {total: 0, completed: 0, open: 0, overdue: 0, critical: 0, crit5: 0, pct: 0};
    for (const it of items) { s.total++; const st = punchStatus(it, today); if (st === 'completed') { s.completed++; continue; } s.open++; if (st === 'overdue') s.overdue++; if (critOpen(it)) { s.critical++; if (+it.critical === 5) s.crit5++; } }
    s.pct = s.total ? Math.round(100 * s.completed / s.total) : 0; return s;
  }
  function fmtDate(iso) { const d = parseISO(iso); return d ? (d.getMonth() + 1) + '/' + d.getDate() + '/' + d.getFullYear() : ''; }
  function fmtDateShort(iso) { const d = parseISO(iso); return d ? (d.getMonth() + 1) + '/' + d.getDate() + '/' + String(d.getFullYear()).slice(2) : ''; }
  function fmtDT(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return iso; return (d.getMonth() + 1) + '/' + d.getDate() + '/' + d.getFullYear() + ' ' + d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'}); }
  function fmtNum(v, dp) { if (v == null || v === '') return '—'; return Number(v).toLocaleString('en-US', {maximumFractionDigits: dp == null ? 0 : dp}); }
  function money(v) { return v == null ? '—' : '$' + Math.round(v).toLocaleString('en-US'); }
  // ---- status pills
  function pillStyle(status, vocab) { const c = (vocab.colors[status] || vocab.colors['']) || ['#fff', '#606a71']; return 'background:' + c[0] + ';color:' + c[1] + (status ? '' : ';border:1px dashed #cbd3de'); }
  function pill(status, vocab, extra) { return '<span class="st-pill' + (status ? '' : ' blank') + '" style="' + pillStyle(status, vocab) + '">' + (status ? esc(status) : 'No status') + (extra || '') + '</span>'; }
  // ---- PM colours: stable per name across the pages
  const pmColors = {};
  function pmColor(name) { const k = String(name || '').toUpperCase(); if (!k) return '#8a94a3'; if (!pmColors[k]) { let h = 0; for (const ch of k) h = (h * 31 + ch.charCodeAt(0)) >>> 0; pmColors[k] = PCA.colors[h % PCA.colors.length]; } return pmColors[k]; }
  // ---- in-page confirm (native dialogs are blocked in embedded browsers)
  function confirm(msg, okLabel) {
    return new Promise(res => {
      const m = document.createElement('div'); m.className = 'fin-modal'; m.style.display = 'flex'; m.style.zIndex = 95;
      m.innerHTML = '<div class="fin-modal-box" style="max-width:460px"><div class="fin-modal-head"><h2>Please confirm</h2></div><p style="margin:0 0 14px;white-space:pre-wrap">' + esc(msg) + '</p><div class="row" style="justify-content:flex-end"><button class="btn" data-x="0">Cancel</button><button class="btn primary" data-x="1">' + esc(okLabel || 'OK') + '</button></div></div>';
      document.body.appendChild(m);
      m.addEventListener('click', e => { const b = e.target.closest('button[data-x]'); if (b) { m.remove(); res(b.dataset.x === '1'); } else if (e.target === m) { m.remove(); res(false); } });
      document.addEventListener('keydown', function onk(e) { if (e.key === 'Escape') { m.remove(); res(false); document.removeEventListener('keydown', onk); } });
      setTimeout(() => m.querySelector('[data-x="1"]').focus(), 30);
    });
  }
  function pref(k, d) { return (typeof PCA !== 'undefined' && PCA.pref) ? PCA.pref(k, d) : d; }
  function setPref(k, v) { if (typeof PCA !== 'undefined' && PCA.setPref) PCA.setPref(k, v); }
  function debounce(fn, ms) { let t; return function () { clearTimeout(t); const a = arguments, s = this; t = setTimeout(() => fn.apply(s, a), ms); }; }
  function download(url) { const a = document.createElement('a'); a.href = url; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); }
  function sortBy(rows, key, dir, getter) { const g = getter || (r => r[key]); return rows.slice().sort((a, b) => { const x = g(a), y = g(b); if (x == null || x === '') return (y == null || y === '') ? 0 : 1; if (y == null || y === '') return -1; return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true, sensitivity: 'base'})) * dir; }); }
  function hoverCard(target, html) {
    let el = document.getElementById('pl-hover'); if (!el) { el = document.createElement('div'); el.id = 'pl-hover'; el.className = 'pl-hover'; document.body.appendChild(el); }
    el.innerHTML = html; el.style.display = 'block';
    const r = target.getBoundingClientRect(); const w = 330;
    el.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 10)) + 'px';
    el.style.top = (r.bottom + 6 + el.offsetHeight > window.innerHeight ? Math.max(8, r.top - el.offsetHeight - 6) : r.bottom + 6) + 'px';
  }
  function hideHover() { const el = document.getElementById('pl-hover'); if (el) el.style.display = 'none'; }
  return {esc, post, toast, todayISO, clock, parseISO, daysUntil, priority, startingSoon, PRIORITY_LABELS, PRIORITY_COLORS, punchStatus, critOpen, punchStats,
          fmtDate, fmtDateShort, fmtDT, fmtNum, money, pillStyle, pill, pmColor, confirm, pref, setPref, debounce, download, sortBy, hoverCard, hideHover};
})();
document.addEventListener('DOMContentLoaded', () => PL.clock(document.getElementById('pl-clock')));
