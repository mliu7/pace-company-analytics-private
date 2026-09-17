/* Documents page (apps/documents): the browse table is fed by /documents/data/ (every live file, no row cap) and
   filtered, faceted, sorted and column-chosen in the browser. Every choice persists: filters in the querystring
   (PCA.rememberFilters so the sidebar link reopens the same view), columns + sort through PCA.pref. */
window.PCADocs = (function () {
  const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const hasPref = () => typeof PCA !== 'undefined' && typeof PCA.pref === 'function';
  const pref = (k, d) => hasPref() ? PCA.pref(k, d) : d;
  const setPref = (k, v) => { if (hasPref()) PCA.setPref(k, v); };
  const COLS = [
    ['name', 'File', {fixed: true}], ['kind', 'Type'], ['size', 'Size', {num: true}], ['mtime', 'Modified', {num: true}], ['by', 'By'],
    ['loc', 'Location'], ['repo', 'Library'], ['folder', 'Folder'], ['div', 'Div'], ['proj', 'Job'], ['ptitle', 'Job title'], ['bname', 'Bid'],
    ['cust', 'Customer'], ['link', 'Link'], ['conf', 'Conf.', {num: true}], ['rule', 'Rule'], ['text', 'Text'], ['nfind', 'Findings', {num: true}]];
  const DEFAULT_ON = {name: 1, kind: 1, size: 1, mtime: 1, by: 1, loc: 1, folder: 1, proj: 1, ptitle: 0, bname: 1, cust: 0, link: 1, conf: 1, rule: 0, text: 0, nfind: 1, repo: 0, div: 1};
  const TIPS = {name: 'File name; ↗ opens it at the source (SharePoint with your login, or smb:// for the P: drive — hover for the Windows UNC).',
    kind: 'File type group.', size: 'Size at the source.', mtime: 'The source\'s own modified time and how long ago.', by: 'Last modified by (SharePoint only).',
    loc: 'SharePoint or the P: drive.', repo: 'The library (SharePoint site) or the share.', folder: 'Folder path inside the library.', div: 'Division of the linked job / bid, else of the library.',
    proj: 'The SL job the file is linked to.', ptitle: 'SL title of the linked job.', bname: 'The bid (Project List row) the file is linked to.', cust: 'Customer the file is linked to directly.',
    link: 'linked = number in the name or a confirmed link · check = a weaker rule, confirm/reject on the file page · unlinked = nothing names a job.',
    conf: 'Confidence of the rule that made the link (under ' + '80% is a check).', rule: 'The linking rule that fired.', text: 'Text extraction status (search + proposal checks).', nfind: 'Open proposal-check findings (errors + warnings).'};
  const AGES = [['7', '7 days'], ['14', '14 days'], ['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['old', 'older']];
  const KINDS = [['pdf', 'PDF'], ['sheet', 'Spreadsheet'], ['doc', 'Document'], ['image', 'Image'], ['email', 'Email'], ['drawing', 'Drawing'], ['note', 'Notes'], ['media', 'Media'], ['archive', 'Archive'], ['other', 'Other']];
  const FACETS = [['loc', 'Location', null], ['repo', 'Library', null], ['div', 'Division', null], ['kind', 'Type', KINDS], ['age', 'Changed', AGES],
    ['link', 'Link', [['linked', 'linked'], ['check', 'check'], ['unlinked', 'unlinked']]], ['text', 'Text', [['ok', 'extracted'], ['none', 'not yet'], ['failed', 'failed'], ['skipped', 'skipped']]],
    ['findings', 'Findings', [['1', 'has findings']]]];
  const S = {rows: [], f: {}, sort: pref('docs.sort', [['mtime', -1]]), cols: Object.assign({}, DEFAULT_ON, pref('docs.cols', {})), opts: {}, limit: 3000};

  const fmtSize = n => n == null ? '' : n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB' : n < 1073741824 ? (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + ' MB' : (n / 1073741824).toFixed(1) + ' GB';
  const fmtDate = iso => { if (!iso) return ''; const d = new Date(iso); return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'}); };
  const ago = d => d == null ? '' : d === 0 ? 'today' : d === 1 ? '1 day' : d < 30 ? d + ' days' : d < 365 ? Math.round(d / 30) + ' mo' : (d / 365).toFixed(1) + ' y';
  const ageKey = d => d == null ? 'old' : d <= 7 ? '7' : d <= 14 ? '14' : d <= 30 ? '30' : d <= 90 ? '90' : d <= 365 ? '365' : 'old';
  const ageMatch = (r, v) => v === 'old' ? (r.age == null || r.age > 365) : (r.age != null && r.age <= +v);
  const rowVal = (r, k) => k === 'age' ? ageKey(r.age) : k === 'findings' ? (r.nfind ? '1' : '') : (r[k] == null ? '' : String(r[k]));
  const passes = (r, skip) => {
    for (const k in S.f) {
      const v = S.f[k]; if (!v || k === skip) continue;
      if (k === 'q') { const q = v.toLowerCase(); if (!(r.name + ' ' + r.path + ' ' + r.proj + ' ' + r.ptitle + ' ' + r.bname + ' ' + r.by).toLowerCase().includes(q)) return false; }
      else if (k === 'age') { if (!ageMatch(r, v)) return false; }
      else if (k === 'findings') { if (!r.nfind) return false; }
      else if (rowVal(r, k) !== v) return false;
    }
    return true;
  };

  function syncUrl() {
    const p = new URLSearchParams();
    for (const k in S.f) if (S.f[k]) p.set(k, S.f[k]);
    const qs = p.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    if (typeof PCA !== 'undefined' && PCA.rememberFilters) PCA.rememberFilters();
  }

  function renderFacets() {
    const box = document.getElementById('doc-facets'); if (!box) return;
    let html = '';
    for (const [key, label, fixed] of FACETS) {
      const counts = {};
      for (const r of S.rows) if (passes(r, key)) { const v = rowVal(r, key); if (v !== '') counts[v] = (counts[v] || 0) + 1; }
      let opts = fixed ? fixed.filter(([v]) => counts[v] || S.f[key] === v) : Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b)).map(v => [v, v]);
      if (!opts.length) continue;
      if (opts.length > 14 && !fixed) opts = opts.slice(0, 14);
      html += '<div class="facet"><span class="dim">' + esc(label) + '</span><span class="lchip' + (S.f[key] ? '' : ' on') + '" data-k="' + key + '" data-v="">All</span>' +
        opts.map(([v, l]) => '<span class="lchip' + (S.f[key] === v ? ' on' : '') + (counts[v] ? '' : ' zero') + '" data-k="' + key + '" data-v="' + esc(v) + '" title="' + esc(label + ': ' + l) + '">' + esc(l) + '<small>' + (counts[v] || 0) + '</small></span>').join('') + '</div>';
    }
    box.innerHTML = html;
    box.querySelectorAll('.lchip').forEach(c => c.addEventListener('click', () => { S.f[c.dataset.k] = c.dataset.v; syncUrl(); render(); }));
  }

  function renderCols() {
    const box = document.getElementById('doc-cols'); if (!box) return;
    box.querySelectorAll('.lchip').forEach(c => c.remove());
    for (const [k, label, o] of COLS) {
      if (o && o.fixed) continue;
      const c = document.createElement('span'); c.className = 'lchip' + (S.cols[k] ? ' on' : ''); c.textContent = label; c.title = TIPS[k] || label;
      c.addEventListener('click', () => { S.cols[k] = S.cols[k] ? 0 : 1; setPref('docs.cols', S.cols); renderCols(); renderTable(); });
      box.appendChild(c);
    }
  }

  const cmp = (a, b, k) => {
    let x = a[k], y = b[k];
    if (k === 'mtime') { x = x || ''; y = y || ''; return x < y ? -1 : x > y ? 1 : 0; }
    if (typeof x === 'number' || typeof y === 'number') { if (x == null) return 1; if (y == null) return -1; return x - y; }
    return String(x || '').localeCompare(String(y || ''), undefined, {numeric: true, sensitivity: 'base'});
  };
  function sorted(rows) {
    const keys = S.sort.length ? S.sort : [['mtime', -1]];
    return rows.slice().sort((a, b) => { for (const [k, d] of keys) { const c = cmp(a, b, k); if (c) return c * d; } return 0; });
  }

  function cell(r, k) {
    switch (k) {
      case 'name': return '<td class="fz1 name" title="' + esc(r.path) + '"><a href="' + esc(r.url) + '">' + esc(r.name) + '</a>' + (r.open ? '<a class="ext" href="' + esc(r.open) + '" target="_blank" rel="noopener" title="' + esc(r.loc === 'P: drive' ? 'Open on the P: drive (smb://) · Windows: ' + r.unc : 'Open in SharePoint with your own login') + '">↗</a>' : '') + '</td>';
      case 'kind': return '<td><span class="tag" title="' + esc(r.ext) + '">' + esc(r.ext ? r.ext.toUpperCase() : r.kind) + '</span></td>';
      case 'size': return '<td class="num muted">' + fmtSize(r.size) + '</td>';
      case 'mtime': return '<td class="num muted" title="' + esc(r.mtime || '') + '">' + fmtDate(r.mtime) + (r.age != null ? ' <span class="muted">· ' + ago(r.age) + '</span>' : '') + '</td>';
      case 'proj': return '<td>' + (r.proj ? '<a href="' + esc(r.purl) + '" title="' + esc(r.ptitle) + '">' + esc(r.proj) + '</a>' : '<span class="muted">—</span>') + '</td>';
      case 'bname': return '<td>' + (r.bid ? '<a href="' + esc(r.burl) + '">' + esc(r.bname.length > 44 ? r.bname.slice(0, 43) + '…' : r.bname) + '</a>' : '<span class="muted">—</span>') + '</td>';
      case 'link': return '<td><span class="tag ' + esc(r.link) + '" title="' + esc(r.rule ? (r.rule.replace(/_/g, ' ') + ' · ' + r.via + ' name') : 'no rule matched') + '">' + esc(r.link) + '</span></td>';
      case 'conf': return '<td class="num">' + (r.conf == null ? '<span class="muted">—</span>' : Math.round(r.conf * 100) + '%') + '</td>';
      case 'rule': return '<td class="muted">' + esc(r.rule.replace(/_/g, ' ')) + '</td>';
      case 'text': return '<td><span class="pill ' + (r.text === 'ok' ? 'good' : r.text === 'failed' ? 'bad' : '') + '">' + esc(r.text === 'none' ? 'not yet' : r.text) + '</span></td>';
      case 'nfind': return '<td class="num">' + (r.nfind ? '<a href="' + esc(S.opts.findingsUrl) + '?file=' + r.id + '&severity=all"><b class="neg">' + r.nfind + '</b></a>' : '<span class="muted">—</span>') + '</td>';
      case 'folder': return '<td class="muted folder-cell" title="' + esc(r.folder) + '">' + esc(r.folder.length > 48 ? '…' + r.folder.slice(-47) : r.folder) + '</td>';
      case 'ptitle': case 'cust': case 'by': return '<td class="muted">' + esc(String(r[k] || '').length > 44 ? String(r[k]).slice(0, 43) + '…' : r[k] || '') + '</td>';
      default: return '<td class="muted">' + esc(r[k] == null ? '' : r[k]) + '</td>';
    }
  }

  function renderTable() {
    const table = document.getElementById('doc-table'); if (!table) return;
    const cols = COLS.filter(([k, , o]) => (o && o.fixed) || S.cols[k]);
    const sortIdx = Object.fromEntries(S.sort.map(([k, d], i) => [k, {d, i}]));
    table.tHead.innerHTML = '<tr>' + cols.map(([k, label, o]) => '<th class="' + ((o && o.num) ? 'num ' : '') + ((o && o.fixed) ? 'fz1 ' : '') + (sortIdx[k] ? 'sorted' : '') + '" data-k="' + k + '" title="' + esc(TIPS[k] || label) + ' Click to sort; shift-click adds a second key.">' + esc(label) +
      (sortIdx[k] ? (sortIdx[k].d > 0 ? ' ▴' : ' ▾') + (S.sort.length > 1 ? '<sup>' + (sortIdx[k].i + 1) + '</sup>' : '') : '') + '</th>').join('') + '</tr>';
    const rows = sorted(S.rows.filter(r => passes(r)));
    const shown = rows.slice(0, S.limit);
    table.tBodies[0].innerHTML = shown.map(r => '<tr data-ref="' + r.id + '">' + cols.map(([k]) => cell(r, k)).join('') + '</tr>').join('') +
      (rows.length > shown.length ? '<tr><td colspan="' + cols.length + '" class="muted" style="text-align:center"><a href="#" id="doc-more">Show all ' + rows.length.toLocaleString() + ' rows</a> (first ' + shown.length.toLocaleString() + ' shown for speed — the data is all here)</td></tr>' : '');
    const count = document.getElementById('doc-count');
    if (count) count.textContent = rows.length.toLocaleString() + ' of ' + S.rows.length.toLocaleString() + ' files' + (Object.values(S.f).some(v => v) ? ' match' : '');
    table.tHead.querySelectorAll('th').forEach(th => th.addEventListener('click', e => {
      const k = th.dataset.k, cur = S.sort.find(s => s[0] === k);
      if (e.shiftKey) { if (cur) cur[1] = -cur[1]; else S.sort.push([k, k === 'mtime' || k === 'size' || k === 'nfind' ? -1 : 1]); }
      else S.sort = [[k, cur && S.sort.length === 1 ? -cur[1] : (k === 'mtime' || k === 'size' || k === 'nfind' || k === 'conf' ? -1 : 1)]];
      setPref('docs.sort', S.sort); renderTable();
    }));
    const more = document.getElementById('doc-more'); if (more) more.addEventListener('click', e => { e.preventDefault(); S.limit = Infinity; renderTable(); });
  }

  function render() { renderFacets(); renderTable(); }

  function init(opts) {
    S.opts = opts || {};
    const preset = S.opts.preset || {};
    for (const k of ['repo', 'project', 'bid', 'customer', 'link', 'kind', 'age', 'q', 'text', 'findings']) if (preset[k]) S.f[k] = preset[k];
    const q = document.getElementById('doc-q');
    if (q) { q.value = S.f.q || ''; let t; q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { S.f.q = q.value.trim(); syncUrl(); render(); }, 150); }); }
    const reset = document.getElementById('doc-reset');
    if (reset) reset.addEventListener('click', () => { S.f = {}; if (q) q.value = ''; syncUrl(); load(); });
    document.querySelectorAll('.doc-kpi[data-filter], .doc-repo-row[data-filter]').forEach(el => el.addEventListener('click', () => {
      const [k, v] = el.dataset.filter.split('=');
      if (k === 'repo') { S.f = {}; S.f.rkey = decodeURIComponent(v); }
      else { S.f = {}; if (v) S.f[k] = v; }
      syncUrl(); document.getElementById('browse').scrollIntoView({behavior: 'smooth', block: 'start'}); load();
    }));
    renderCols();
    load();
  }

  function load() {
    // server-side narrowing for the deep-link keys (project / bid / customer / repo key); everything else is client-side
    const p = new URLSearchParams();
    for (const k of ['project', 'bid', 'customer']) if (S.f[k]) p.set(k, S.f[k]);
    if (S.f.rkey) p.set('repo', S.f.rkey);
    const count = document.getElementById('doc-count'); if (count) count.textContent = 'loading…';
    fetch(S.opts.url + (p.toString() ? '?' + p.toString() : '')).then(r => r.json()).then(res => { S.rows = res.rows; render(); })
      .catch(() => { if (count) count.textContent = 'could not load the file list'; });
  }

  return {init};
})();
