// Global search: the sidebar box (base.html #gsearch) and its floating results panel.
//
// Type → a debounced call to /search/suggest/ → grouped results (projects, people, customers, vendors, 010 orders,
// divisions, pages) in a panel that floats beside the sidebar. ↑ ↓ move, ↵ opens, Esc closes, "Show all" goes to
// /search/?q=… . ⌘K / Ctrl-K or "/" focus the box from anywhere; when the sidebar is collapsed to its icon rail the
// magnifier opens the same panel as a centred palette with its own input. The last things you opened from the panel
// are remembered per browser (localStorage) and offered when the box is empty.
(function () {
  const input = document.getElementById('gsearch-input'), rail = document.getElementById('gsearch-rail');
  if (!input) return;
  const SUGGEST = input.dataset.suggest, PAGE = input.dataset.page, RECENT_KEY = 'pca-search-recent-v1';
  const layout = document.querySelector('.layout'), sidebar = document.querySelector('.sidebar');
  const GROUP_ICON = {project: '▤', person: '☺', customer: '▣', vendor: '⇡', order: '≡', document: '⌗', division: '◈', page: '→', recent: '↺'};
  let panel, head, pInput, body, foot, footAll, footHint, mode = 'anchored', isOpen = false;
  let items = [], sel = -1, timer = null, ctrl = null, lastQ = null;
  const cache = new Map();

  // ---------- helpers
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const tokens = q => (q || '').toLowerCase().split(/\s+/).filter(Boolean);
  function hl(text, toks) {   // escape, then wrap every token hit in <mark>
    const s = esc(text);
    if (!toks.length) return s;
    const re = new RegExp('(' + toks.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'ig');
    return s.replace(re, '<mark>$1</mark>');
  }
  const recent = () => { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]') || []; } catch (e) { return []; } };
  function remember(it) {
    const list = recent().filter(r => r.url !== it.url);
    list.unshift({type: it.type, title: it.title, sub: it.sub, url: it.url, ava: it.ava});
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8))); } catch (e) {}
  }
  const activeInput = () => (mode === 'palette' ? pInput : input);

  // ---------- panel
  function build() {
    if (panel) return;
    panel = document.createElement('div'); panel.className = 'gs-panel'; panel.hidden = true;
    panel.innerHTML = '<div class="gs-head"><span class="gs-ico">⌕</span><input type="search" placeholder="Search projects, people, customers, vendors, pages…" autocomplete="off" spellcheck="false" aria-label="Search"></div>' +
      '<div class="gs-body"></div>' +
      '<div class="gs-foot"><span class="gs-hint"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>↵</kbd> open · <kbd>esc</kbd> close</span><a class="gs-all" href="#"></a></div>';
    document.body.appendChild(panel);
    head = panel.querySelector('.gs-head'); pInput = head.querySelector('input'); body = panel.querySelector('.gs-body');
    foot = panel.querySelector('.gs-foot'); footAll = foot.querySelector('.gs-all'); footHint = foot.querySelector('.gs-hint');
    pInput.addEventListener('input', () => query(pInput.value));
    pInput.addEventListener('keydown', onKey);
    body.addEventListener('mousemove', e => { const a = e.target.closest('.gs-item'); if (a) select(items.indexOf(a), false); });
    body.addEventListener('click', e => { const a = e.target.closest('.gs-item'); if (a && a.dataset.item) remember(JSON.parse(a.dataset.item)); });
    document.addEventListener('mousedown', e => { if (isOpen && !panel.contains(e.target) && !e.target.closest('#gsearch')) close(); });
    window.addEventListener('resize', () => { if (isOpen) place(); });
    sidebar.addEventListener('scroll', () => { if (isOpen && mode === 'anchored') place(); });
  }
  function place() {
    if (mode === 'palette') { panel.classList.add('palette'); panel.style.left = panel.style.top = panel.style.maxHeight = ''; return; }
    panel.classList.remove('palette');
    const r = input.getBoundingClientRect(), sb = sidebar.getBoundingClientRect();
    const top = Math.max(8, Math.round(r.top) - 6);
    panel.style.left = Math.round(sb.right + 10) + 'px';
    panel.style.top = top + 'px';
    panel.style.maxHeight = Math.max(240, Math.min(660, window.innerHeight - top - 14)) + 'px';
  }
  function open(m) {
    build();
    mode = m || (layout.classList.contains('nav-collapsed') ? 'palette' : 'anchored');
    head.hidden = mode !== 'palette';
    panel.hidden = false; isOpen = true; place();
    requestAnimationFrame(() => panel.classList.add('show'));
    if (mode === 'palette') { pInput.value = input.value; pInput.focus(); pInput.select(); }
    query(activeInput().value, true);
  }
  function close() {
    if (!panel || !isOpen) return;
    isOpen = false; panel.classList.remove('show');
    setTimeout(() => { if (!isOpen) panel.hidden = true; }, 140);
    if (mode === 'palette') { input.value = pInput.value; }
  }

  // ---------- data
  function query(q, immediate) {
    q = (q || '').trim();
    clearTimeout(timer);
    const run = () => {
      if (q === lastQ && cache.has(q)) { render(cache.get(q), q); return; }
      if (cache.has(q)) { lastQ = q; render(cache.get(q), q); return; }
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      panel.classList.add('loading');
      fetch(SUGGEST + '?q=' + encodeURIComponent(q), {signal: ctrl.signal, credentials: 'same-origin', headers: {'Accept': 'application/json'}})
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then(d => { cache.set(q, d); if (cache.size > 200) cache.delete(cache.keys().next().value); lastQ = q; if (activeInput().value.trim() === q) render(d, q); })
        .catch(e => { if (e && e.name === 'AbortError') return; body.innerHTML = '<div class="gs-empty"><b>Search is unavailable</b>Try again in a moment.</div>'; })
        .finally(() => panel.classList.remove('loading'));
    };
    if (immediate || !q) run(); else timer = setTimeout(run, 90);
  }

  // ---------- rendering
  function itemHTML(it, toks) {
    const ava = it.ava || (it.title || '?').trim().slice(0, 1).toUpperCase();
    const tags = (it.tags || []).map(t => '<span class="gs-tag ' + esc(t.c || '') + '">' + esc(t.t) + '</span>').join('');
    return '<a class="gs-item" href="' + esc(it.url) + '" data-item="' + esc(JSON.stringify({type: it.type, title: it.title, sub: it.sub, url: it.url, ava: it.ava})) + '">' +
      '<span class="gs-ava ' + esc(it.type) + '">' + esc(ava) + '</span>' +
      '<span class="gs-main"><span class="gs-title">' + (it.number ? '<span class="gs-num">' + hl(it.number, toks) + '</span> ' : '') + hl(it.title, toks) + '</span>' +
      (it.sub ? '<span class="gs-sub">' + hl(it.sub, toks) + '</span>' : '') + '</span>' +
      '<span class="gs-right">' + tags + (it.meta ? '<span class="gs-meta">' + esc(it.meta) + '</span>' : '') + '<span class="gs-enter">↵</span></span></a>';
  }
  function groupHTML(g, toks) {
    const more = g.count > g.items.length ? '<span class="gs-more">+' + (g.count - g.items.length) + ' more</span>' : '';
    return '<div class="gs-group gs-' + esc(g.type) + '"><div class="gs-group-h"><span class="gs-gico">' + (GROUP_ICON[g.type] || '·') + '</span>' + esc(g.label) +
      '<span class="n">' + g.count + (g.capped ? '+' : '') + '</span>' + more + '</div>' + g.items.map(it => itemHTML(it, toks)).join('') + '</div>';
  }
  function render(d, q) {
    const toks = tokens(q);
    let html = '';
    if (!q) {
      const rec = recent();
      if (rec.length) html += groupHTML({type: 'recent', label: 'Recent', count: rec.length, items: rec}, []);
      (d.groups || []).forEach(g => { html += groupHTML(g, []); });
      if (!html) html = '<div class="gs-empty"><b>Search everything</b>Project numbers and titles, people, customers, vendors, 010 orders, pages.</div>';
      footAll.hidden = true; footHint.hidden = false;
    } else if (!d.total) {
      html = '<div class="gs-empty"><b>Nothing matched “' + esc(q) + '”</b>Try fewer words, a project number, or part of a name.</div>';
      footAll.hidden = true; footHint.hidden = false;
    } else {
      (d.groups || []).forEach(g => { html += groupHTML(g, toks); });
      footAll.hidden = false; footHint.hidden = false;
      footAll.href = PAGE + '?q=' + encodeURIComponent(q);
      footAll.innerHTML = 'Show all ' + d.total + (d.capped ? '+' : '') + ' result' + (d.total === 1 ? '' : 's') + ' <span class="arr">→</span>';
    }
    body.innerHTML = html;
    items = [...body.querySelectorAll('.gs-item')];
    select(items.length && q ? 0 : -1, false);
    body.scrollTop = 0;
  }
  function select(i, scroll) {
    items.forEach(a => a.classList.remove('sel'));
    sel = i;
    if (i >= 0 && items[i]) { items[i].classList.add('sel'); if (scroll !== false) items[i].scrollIntoView({block: 'nearest'}); }
  }

  // ---------- keys
  function onKey(e) {
    if (!isOpen && (e.key === 'ArrowDown' || e.key === 'Enter')) { open(); }
    if (!isOpen) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) select((sel + 1) % items.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) select((sel - 1 + items.length) % items.length); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const a = sel >= 0 ? items[sel] : null, q = activeInput().value.trim();
      if (a) { if (a.dataset.item) remember(JSON.parse(a.dataset.item)); if (e.metaKey || e.ctrlKey) window.open(a.href, '_blank'); else location.href = a.href; }
      else if (q) location.href = PAGE + '?q=' + encodeURIComponent(q);
    }
    else if (e.key === 'Escape') { e.preventDefault(); close(); activeInput().blur(); }
    else if (e.key === 'Tab') { close(); }
  }
  input.addEventListener('focus', () => open('anchored'));
  input.addEventListener('input', () => { if (!isOpen) open('anchored'); query(input.value); });
  input.addEventListener('keydown', onKey);
  if (rail) rail.addEventListener('click', () => { if (isOpen) close(); else open('palette'); });
  document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase(), typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (isOpen) { close(); activeInput().blur(); return; }
      if (layout.classList.contains('nav-collapsed')) open('palette'); else { input.focus(); input.select(); }
    } else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      if (layout.classList.contains('nav-collapsed')) open('palette'); else { input.focus(); input.select(); }
    }
  });
})();
