/* Estimate builder: rooms, lines (area / qty / cost / markup ↔ sell / 11 labor-hour inputs), column chooser, live totals,
   Estimator Notes warnings, quick add, versioned save with the stale-form check. Maths mirror apps/estimating/rules.py:
   sell = cost × markup (2 dp) / markup = sell ÷ cost (3 dp); labor hours are per line — never × qty. */
(function () {
  const CFG = JSON.parse(document.getElementById('est-cfg').textContent);
  let P = JSON.parse(document.getElementById('est-payload').textContent);
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const fmt = v => '$' + Number(v || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const fmt0 = v => '$' + Number(v || 0).toLocaleString('en-US', {maximumFractionDigits: 0});
  const csrf = () => { const m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ''; };
  const r2 = v => Math.round((+v || 0) * 100) / 100, r3 = v => Math.round((+v || 0) * 1000) / 1000;
  const LT = P.labor_types, MK = +P.markup_default || 1.265, WRITE = CFG.can_write;
  const toastEl = $('toast'); let toastT;
  const toast = m => { toastEl.textContent = m; toastEl.classList.add('on'); clearTimeout(toastT); toastT = setTimeout(() => toastEl.classList.remove('on'), 2200); };
  const prefKey = 'est.' + P.id;
  let cur = PCA.pref(prefKey + '.room', 0), dirty = false, saving = false, metaT, quickT, quickRows = [], quickIdx = -1, nid = -1;
  const GROUPS = [['eq', 'Equipment', 'Qty, dealer cost, cost ext, markup, sell, sell ext'], ['uh', 'Union hours', 'Mobilization, field labor, rough, pull, trim, test — hours per line, not × qty'],
                  ['nh', 'Non-union hours', 'Engineering, fabrication, programming, commissioning, service — hours per line'], ['ld', 'Labor $ by type', 'Cost and sell per labor type at the rate card'],
                  ['ls', 'Labor totals', 'Σ labor cost / sell'], ['tot', 'Line totals', 'Total cost, total sell, profit, margin']];
  const cols = Object.assign({eq: true, uh: true, nh: true, ld: false, ls: true, tot: true}, PCA.pref('cols', {}) || {});
  const MISC_RE = /cable|wire|connector|adapter|plate|bulk|misc|material|consumable|hardware|freight|cat6|cat5|hdmi|hdbaset|db9|patch|sleeving|tie holder|j-hook|name plate|trim/;
  const NOT_MISC_RE = /display|camera|processor|amplifier|speaker|touchpanel|microphone|switcher|dsp|rack$/;

  // ---- maths
  function rate(id) { const t = LT.find(x => x.id === id); const r = t && P.rates[t.rate_id]; return r || {cost: 0, sell: 0}; }
  function calc(l) {
    const qty = Math.max(1, parseInt(l.qty) || 1), cost = Math.max(0, +l.cost || 0), sell = Math.max(0, +l.sell || 0);
    let lc = 0, ls = 0, lh = 0; const labor = {};
    for (const t of LT) { const h = Math.max(0, +(l.hours || {})[t.id] || 0), r = rate(t.id); labor[t.id] = {hours: h, cost: h * r.cost, sell: h * r.sell}; lc += h * r.cost; ls += h * r.sell; lh += h; }
    const ec = cost * qty, es = sell * qty, tc = ec + lc, ts = es + ls;
    return {qty, cost, sell, ec, es, lc, ls, lh, tc, ts, profit: ts - tc, margin: ts > 0 ? (ts - tc) / ts * 100 : null, labor};
  }
  function sumT(ts) { const o = {ec: 0, es: 0, lc: 0, ls: 0, lh: 0, tc: 0, ts: 0, n: ts.length, hours: {}}; for (const t of ts) { for (const k of ['ec', 'es', 'lc', 'ls', 'lh', 'tc', 'ts']) o[k] += t[k]; for (const id in t.labor) o.hours[id] = (o.hours[id] || 0) + t.labor[id].hours; } o.profit = o.ts - o.tc; o.margin = o.ts > 0 ? o.profit / o.ts * 100 : null; return o; }
  function roomT(r) { return sumT(r.lines.map(calc)); }
  function grandT() { const rs = P.rooms.map(roomT); const g = sumT(rs); g.n = rs.reduce((a, r) => a + r.n, 0); g.hours = {}; rs.forEach(r => { for (const id in r.hours) g.hours[id] = (g.hours[id] || 0) + r.hours[id]; }); return g; }
  function isMisc(l) { if (l.misc != null) return !!l.misc; const t = [l.category, l.manufacturer, l.part, l.description].join(' ').toLowerCase(); return MISC_RE.test(t) && !NOT_MISC_RE.test(t); }
  function warnings(g) {
    const out = [];
    if (g.ts > 25000 || g.lh > 80) out.push('Peer review with GE or Field Superintendent required (' + [g.ts > 25000 ? 'sell ' + fmt0(g.ts) + ' > $25,000' : '', g.lh > 80 ? 'labor ' + g.lh.toFixed(1) + ' h > 80 h' : ''].filter(Boolean).join(', ') + ').');
    let low = 0, lowMisc = 0, cons = 0, field = 0;
    P.rooms.forEach(r => r.lines.forEach(l => { if (l.kind === 'note') return; const c = +l.cost || 0, mk = +l.markup || 0; field += +(l.hours || {}).field_labor || 0;
      if (isMisc(l)) { cons += c * Math.max(1, parseInt(l.qty) || 1); if (c > 0 && mk < 1.5) lowMisc++; } else if (c > 0 && mk < MK) low++; }));
    if (low) out.push(low + ' equipment line' + (low > 1 ? 's' : '') + ' below the 1.265 minimum markup.');
    if (lowMisc) out.push(lowMisc + ' consumable line' + (lowMisc > 1 ? 's' : '') + ' below the 1.5 markup.');
    if (cons > 0) { const need = Math.round(cons / 1000 * 8 * 10) / 10; if (field < need) out.push('Consumables cost ' + fmt0(cons) + ' call for ' + need + ' h of miscellaneous union field labor; the estimate carries ' + field.toFixed(1) + ' h.'); }
    return out;
  }

  // ---- render
  function room() { if (cur >= P.rooms.length) cur = 0; return P.rooms[cur]; }
  function renderRooms() {
    const g = grandT();
    $('rooms').innerHTML = P.rooms.map((r, i) => { const t = roomT(r); return '<span class="room' + (i === cur ? ' on' : '') + '" data-room="' + i + '" title="Double-click to rename">' + esc(r.name) + '<small>' + r.lines.length + ' · ' + fmt0(t.ts) + '</small></span>'; }).join('') +
      (WRITE ? '<button class="btn small" id="room-add" title="Add a room">+ Room</button>' : '');
    $('roomcards').innerHTML = P.rooms.map((r, i) => { const t = roomT(r); return '<div class="roomcard' + (i === cur ? ' on' : '') + '" data-room="' + i + '"><b>' + esc(r.name) + '</b><small>' + r.lines.length + ' item' + (r.lines.length === 1 ? '' : 's') + ' · cost ' + fmt0(t.tc) + ' · sell ' + fmt0(t.ts) + ' · ' + t.lh.toFixed(1) + ' h</small></div>'; }).join('') +
      '<div class="roomcard" style="background:#f7f9fc;cursor:default"><b>Full estimate</b><small>' + P.rooms.length + ' room' + (P.rooms.length === 1 ? '' : 's') + ' · ' + g.n + ' items · <b>sell ' + fmt0(g.ts) + '</b></small></div>';
    const r = room(); if ($('room-name')) $('room-name').value = r ? r.name : ''; if ($('room-notes')) $('room-notes').value = r ? (r.notes || '') : '';
  }
  function th(label, title, cls) { return '<th class="' + (cls || '') + '" title="' + esc(title) + '">' + label + '</th>'; }
  function renderCols() {
    $('cols').querySelectorAll('.lchip[data-g]').forEach(c => c.classList.toggle('on', !!cols[c.dataset.g]));
    const tb = $('eb');
    tb.querySelectorAll('[data-cg]').forEach(el => { el.hidden = !cols[el.dataset.cg]; });
    tb.querySelectorAll('thead tr.bands th[data-g]').forEach(el => { el.hidden = !cols[el.dataset.g]; });
  }
  function renderTable() {
    const r = room(); if (!r) return;
    const uh = LT.filter(t => t.group === 'union'), nh = LT.filter(t => t.group === 'non_union');
    let h = '<thead><tr class="bands"><th class="fz fz1" colspan="2" data-x>Item</th><th data-x></th>' +
      '<th data-g="eq" colspan="6" title="Click to collapse — bring it back with the Columns chips">Equipment</th><th data-g="uh" colspan="6" class="gs">Union hours</th><th data-g="nh" colspan="5" class="gs">Non-union hours</th>' +
      '<th data-g="ld" colspan="' + (LT.length * 2) + '" class="gs">Labor $ by type</th><th data-g="ls" colspan="2" class="gs">Labor</th><th data-g="tot" colspan="4" class="gs">Line totals</th><th data-x></th></tr><tr>' +
      '<th class="fz fz1">Vendor / Part #</th><th class="fz fz2">Description</th>' + th('Area', 'Room / area label on the line (defaults to the last area, then the estimate title)') +
      '<th data-cg="eq" class="num" title="Quantity — multiplies equipment cost and sell, never labor hours">Qty</th><th data-cg="eq" class="num" title="Dealer cost each (from the catalog; editable)">Cost</th><th data-cg="eq" class="num" title="Cost × qty">Cost ext</th>' +
      '<th data-cg="eq" class="num" title="Sell ÷ cost (3 dp). Editing it recomputes sell; policy minimum 1.265 (1.5 on consumables)">Markup</th><th data-cg="eq" class="num" title="Sell each (2 dp). Editing it recomputes markup">Sell</th><th data-cg="eq" class="num" title="Sell × qty">Sell ext</th>';
    uh.forEach((t, i) => { h += '<th data-cg="uh" class="num' + (i === 0 ? ' gs' : '') + '" title="' + esc(t.label) + ' — total billable hours for this line (NOT × qty) at $' + rate(t.id).cost + ' → $' + rate(t.id).sell + '/h">' + esc(t.short) + '</th>'; });
    nh.forEach((t, i) => { h += '<th data-cg="nh" class="num' + (i === 0 ? ' gs' : '') + '" title="' + esc(t.label) + ' — total billable hours for this line (NOT × qty) at $' + rate(t.id).cost + ' → $' + rate(t.id).sell + '/h">' + esc(t.short) + '</th>'; });
    LT.forEach((t, i) => { h += '<th data-cg="ld" class="num' + (i === 0 ? ' gs' : '') + '" title="' + esc(t.label) + ' hours × $' + rate(t.id).cost + '">' + esc(t.short) + ' cost</th><th data-cg="ld" class="num" title="' + esc(t.label) + ' hours × $' + rate(t.id).sell + '">' + esc(t.short) + ' sell</th>'; });
    h += '<th data-cg="ls" class="num gs" title="Σ hours × cost rate">Labor cost</th><th data-cg="ls" class="num" title="Σ hours × sell rate">Labor sell</th>' +
      '<th data-cg="tot" class="num gs" title="Cost ext + labor cost">Total cost</th><th data-cg="tot" class="num" title="Sell ext + labor sell">Total sell</th><th data-cg="tot" class="num" title="Total sell − total cost">Profit</th><th data-cg="tot" class="num" title="Profit ÷ total sell">Margin</th><th></th></tr></thead><tbody>';
    r.lines.forEach((l, j) => { h += rowHtml(l, j); });
    if (!r.lines.length) h += '<tr><td colspan="40" class="empty">No lines in this room yet — use the quick add above, or the catalog search.</td></tr>';
    const t = roomT(r);
    h += '</tbody><tfoot><tr><td class="fz fz1"><b>Room total</b></td><td class="fz fz2">' + r.lines.length + ' line' + (r.lines.length === 1 ? '' : 's') + '</td><td></td><td data-cg="eq"></td><td data-cg="eq"></td><td data-cg="eq" class="num">' + fmt(t.ec) + '</td><td data-cg="eq"></td><td data-cg="eq"></td><td data-cg="eq" class="num">' + fmt(t.es) + '</td>';
    uh.forEach((x, i) => { h += '<td data-cg="uh" class="num' + (i === 0 ? ' gs' : '') + '">' + (t.hours[x.id] || 0).toFixed(2) + '</td>'; });
    nh.forEach((x, i) => { h += '<td data-cg="nh" class="num' + (i === 0 ? ' gs' : '') + '">' + (t.hours[x.id] || 0).toFixed(2) + '</td>'; });
    LT.forEach((x, i) => { const hh = t.hours[x.id] || 0, rr = rate(x.id); h += '<td data-cg="ld" class="num' + (i === 0 ? ' gs' : '') + '">' + fmt(hh * rr.cost) + '</td><td data-cg="ld" class="num">' + fmt(hh * rr.sell) + '</td>'; });
    h += '<td data-cg="ls" class="num gs">' + fmt(t.lc) + '</td><td data-cg="ls" class="num">' + fmt(t.ls) + '</td><td data-cg="tot" class="num gs">' + fmt(t.tc) + '</td><td data-cg="tot" class="num">' + fmt(t.ts) + '</td><td data-cg="tot" class="num">' + fmt(t.profit) + '</td><td data-cg="tot" class="num">' + (t.margin == null ? '' : t.margin.toFixed(1) + '%') + '</td><td></td></tr></tfoot>';
    $('eb').innerHTML = h;
    renderCols();
  }
  function inp(l, j, f, cls, step, min) { return WRITE ? '<input class="' + (cls || '') + '" data-j="' + j + '" data-f="' + f + '" value="' + esc(l[f] == null ? '' : l[f]) + '" ' + (step ? 'type="number" step="' + step + '" min="' + (min == null ? 0 : min) + '"' : '') + '>' : '<span>' + esc(l[f] == null ? '' : l[f]) + '</span>'; }
  function hinp(l, j, id) { const v = (l.hours || {})[id] || 0; return WRITE ? '<input class="h" data-j="' + j + '" data-h="' + id + '" type="number" step="0.25" min="0" value="' + (v ? v : '') + '" placeholder="0">' : '<span>' + (v || '') + '</span>'; }
  function rowHtml(l, j) {
    const t = calc(l), note = l.kind === 'note', uh = LT.filter(x => x.group === 'union'), nh = LT.filter(x => x.group === 'non_union');
    let h = '<tr data-j="' + j + '" class="' + (note ? 'note' : '') + '"><td class="fz fz1"><div class="muted" style="font-size:11px">' + esc(l.manufacturer) + (isMisc(l) && !note ? ' <span class="tag" title="Consumable / misc material — policy markup 1.5">misc</span>' : '') + '</div><b class="mono">' + (l.catalog_item_id ? '<a href="' + CFG.item_url.replace('/0/', '/' + l.catalog_item_id + '/') + '" title="Open this part in the catalog (price history)">' + esc(l.part) + '</a>' : esc(l.part) || (note ? 'NOTE' : '—')) + '</b></td>' +
      '<td class="fz fz2 desc" title="' + esc(l.description) + (l.source_name ? ' · ' + esc(l.source_name) : '') + (l.date_label ? ' · ' + esc(l.date_label) : '') + '">' + (WRITE && (note || !l.catalog_item_id) ? inp(l, j, 'description', 'wide') : esc(l.description)) + '</td>' +
      '<td>' + inp(l, j, 'area', 'wide') + '</td>';
    if (note) { h += '<td data-cg="eq" colspan="6" class="muted">note line — no price, no labor</td>' + '<td data-cg="uh" colspan="6" class="gs"></td><td data-cg="nh" colspan="5" class="gs"></td><td data-cg="ld" colspan="' + (LT.length * 2) + '" class="gs"></td><td data-cg="ls" colspan="2" class="gs"></td><td data-cg="tot" colspan="4" class="gs"></td>'; }
    else {
      h += '<td data-cg="eq" class="num">' + inp(l, j, 'qty', '', '1', 1) + '</td><td data-cg="eq" class="num">' + inp(l, j, 'cost', '', '0.01') + '</td><td data-cg="eq" class="num" data-v="ec">' + fmt(t.ec) + '</td>' +
        '<td data-cg="eq" class="num">' + inp(l, j, 'markup', '', '0.001') + '</td><td data-cg="eq" class="num">' + inp(l, j, 'sell', '', '0.01') + '</td><td data-cg="eq" class="num" data-v="es">' + fmt(t.es) + '</td>';
      uh.forEach((x, i) => { h += '<td data-cg="uh" class="num' + (i === 0 ? ' gs' : '') + '">' + hinp(l, j, x.id) + '</td>'; });
      nh.forEach((x, i) => { h += '<td data-cg="nh" class="num' + (i === 0 ? ' gs' : '') + '">' + hinp(l, j, x.id) + '</td>'; });
      LT.forEach((x, i) => { h += '<td data-cg="ld" class="num' + (i === 0 ? ' gs' : '') + '" data-v="lc_' + x.id + '">' + fmt(t.labor[x.id].cost) + '</td><td data-cg="ld" class="num" data-v="ls_' + x.id + '">' + fmt(t.labor[x.id].sell) + '</td>'; });
      h += '<td data-cg="ls" class="num gs" data-v="lc">' + fmt(t.lc) + '</td><td data-cg="ls" class="num" data-v="ls">' + fmt(t.ls) + '</td>' +
        '<td data-cg="tot" class="num gs" data-v="tc">' + fmt(t.tc) + '</td><td data-cg="tot" class="num" data-v="ts">' + fmt(t.ts) + '</td><td data-cg="tot" class="num" data-v="profit">' + fmt(t.profit) + '</td><td data-cg="tot" class="num" data-v="margin">' + (t.margin == null ? '' : t.margin.toFixed(1) + '%') + '</td>';
    }
    h += '<td class="act">' + (WRITE ? '<button data-act="up" title="Move up">↑</button><button data-act="down" title="Move down">↓</button><button data-act="dup" title="Duplicate line">⧉</button><button data-act="rm" title="Remove line">×</button>' : '') + '</td></tr>';
    return h;
  }
  function patchRow(j) {
    const r = room(), l = r.lines[j], tr = $('eb').querySelector('tr[data-j="' + j + '"]'); if (!tr) return;
    const t = calc(l);
    const set = (k, v) => { const td = tr.querySelector('[data-v="' + k + '"]'); if (td) { td.textContent = v; td.classList.add('dirty'); } };
    set('ec', fmt(t.ec)); set('es', fmt(t.es)); set('lc', fmt(t.lc)); set('ls', fmt(t.ls)); set('tc', fmt(t.tc)); set('ts', fmt(t.ts)); set('profit', fmt(t.profit)); set('margin', t.margin == null ? '' : t.margin.toFixed(1) + '%');
    LT.forEach(x => { set('lc_' + x.id, fmt(t.labor[x.id].cost)); set('ls_' + x.id, fmt(t.labor[x.id].sell)); });
    const mk = tr.querySelector('input[data-f="markup"]'), sl = tr.querySelector('input[data-f="sell"]');
    if (mk && document.activeElement !== mk) mk.value = l.markup == null ? '' : l.markup; if (sl && document.activeElement !== sl) sl.value = l.sell == null ? '' : l.sell;
  }
  function renderTotals() {
    const r = room(), t = r ? roomT(r) : sumT([]), g = grandT();
    const tile = (l, v, s, ttl) => '<div class="t" title="' + esc(ttl) + '"><div class="l">' + l + '</div><div class="v">' + v + '</div>' + (s ? '<div class="s">' + s + '</div>' : '') + '</div>';
    $('tot-room').innerHTML = tile('Room cost', fmt(t.tc), 'equipment ' + fmt0(t.ec) + ' · labor ' + fmt0(t.lc), 'Cost ext + labor cost for the current room') + tile('Room sell', fmt(t.ts), 'equipment ' + fmt0(t.es) + ' · labor ' + fmt0(t.ls), 'Sell ext + labor sell for the current room') +
      tile('Room profit', fmt(t.profit), '', 'Sell − cost') + tile('Room margin', t.margin == null ? '—' : t.margin.toFixed(1) + '%', t.lh.toFixed(1) + ' labor h', 'Profit ÷ sell');
    $('tot-grand').innerHTML = tile('Total cost', fmt(g.tc), 'equipment ' + fmt0(g.ec) + ' · labor ' + fmt0(g.lc), 'All rooms: cost ext + labor cost') + tile('Total sell', fmt(g.ts), 'equipment ' + fmt0(g.es) + ' · labor ' + fmt0(g.ls), 'All rooms: sell ext + labor sell') +
      tile('Profit', fmt(g.profit), '', 'Total sell − total cost') + tile('Margin', g.margin == null ? '—' : g.margin.toFixed(1) + '%', g.lh.toFixed(1) + ' labor h · ' + g.n + ' lines', 'Profit ÷ total sell') +
      (P.bid ? tile('Bid budget / value', (P.bid.budget == null ? '—' : fmt0(P.bid.budget)) + ' / ' + (P.bid.value == null ? '—' : fmt0(P.bid.value)), P.bid.value != null ? 'estimate sell ' + (g.ts - P.bid.value >= 0 ? '+' : '−') + fmt0(Math.abs(g.ts - P.bid.value)) + ' vs bid value' : '', 'What was typed into the Project Portal for the attached bid (Budget = cost, Project Value = price)') : '');
    const w = warnings(g), box = $('warn');
    box.className = 'warnbox' + (w.length ? '' : ' ok');
    box.innerHTML = w.length ? '<b>Estimator Notes</b> — warnings only, saving is never blocked:<ul>' + w.map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '✓ No Estimator Notes warnings (peer review threshold, minimum markups, field labor per $1k consumables).';
  }
  function renderAll() { renderRooms(); renderTable(); renderTotals(); }
  function setDirty(v) { dirty = v; $('save-state').innerHTML = v ? '<span class="dirtypill">unsaved changes</span>' : (P.updated_at ? '<span class="savedpill">Saved ✓ v' + P.version_no + '</span>' : ''); if ($('btn-save')) $('btn-save').classList.toggle('primary', v); }

  // ---- edits
  function onEdit(inputEl) {
    const r = room(), j = +inputEl.dataset.j, l = r.lines[j]; if (!l) return;
    if (inputEl.dataset.h) { l.hours = l.hours || {}; l.hours[inputEl.dataset.h] = Math.max(0, parseFloat(inputEl.value) || 0); }
    else {
      const f = inputEl.dataset.f, v = inputEl.value;
      if (f === 'qty') l.qty = Math.max(1, parseInt(v) || 1);
      else if (f === 'cost') { l.cost = Math.max(0, parseFloat(v) || 0); if (l.cost > 0) l.sell = r2(l.cost * (l.markup || MK)); l.markup = l.cost > 0 ? r3(l.sell / l.cost) : l.markup; }
      else if (f === 'markup') { l.markup = Math.max(0, parseFloat(v) || MK); if (+l.cost > 0) l.sell = r2(l.cost * l.markup); }
      else if (f === 'sell') { l.sell = Math.max(0, parseFloat(v) || 0); if (+l.cost > 0) l.markup = r3(l.sell / l.cost); }
      else l[f] = v;
      if (f === 'qty' || f === 'cost') inputEl.value = l[f];
    }
    patchRow(j); renderTotals(); renderRooms(); setDirty(true);
  }
  function addLine(row) {
    const r = room(); if (!r) return;
    const last = r.lines[r.lines.length - 1];
    const cost = row.cost || 0, sell = cost ? r2(cost * MK) : (row.msrp || 0);
    r.lines.push({id: nid--, catalog_item_id: row.id, kind: 'item', area: last ? last.area : (P.title || r.name), manufacturer: row.mfr, part: row.part, part_norm: row.norm, description: row.desc, category: row.category,
                  source_name: row.source, date_key: row.dk, date_label: row.date, item_msrp: row.msrp, item_map: row.map, qty: 1, cost, markup: cost ? MK : null, sell, misc: row.misc, hours: {}});
    renderAll(); setDirty(true); toast('Added line: ' + row.part);
  }

  // ---- save / meta
  function payload() {
    return {action: 'save', id: P.id, version_no: P.version_no, title: $('meta-title').value, client_name: $('meta-client').value, notes: $('meta-notes').value, status: $('meta-status').value, project_number: $('meta-project').value,
            rooms: P.rooms.map((r, i) => ({id: r.id > 0 ? r.id : null, name: r.name, order: i, notes: r.notes || '', lines: r.lines.map((l, j) => ({...l, id: l.id > 0 ? l.id : null, order: j, totals: undefined}))}))};
  }
  function save() {
    if (!WRITE || saving) return;
    saving = true; $('btn-save').textContent = 'Saving…';
    fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify(payload())})
      .then(r => r.json().then(res => ({status: r.status, res}))).then(({status, res}) => {
        saving = false; $('btn-save').textContent = 'Save';
        if (status === 409) { $('stale').hidden = false; $('stale').innerHTML = '<b>Not saved:</b> ' + esc(res.error) + '. <a href="">Reload</a> to see the latest version (your edits are lost on reload — export first if you need them).'; return; }
        if (!res.ok) { toast(res.error || 'save failed'); return; }
        P = res.payload; cur = Math.min(cur, P.rooms.length - 1); renderAll(); setDirty(false); $('stale').hidden = true;
        toast('Saved v' + P.version_no + ' at ' + res.saved_at);
        if (res.payload.warnings && res.payload.warnings.length) { /* server-side policy text mirrors the live box */ }
        const vl = $('versions'); if (vl) vl.insertAdjacentHTML('afterbegin', '<tr><td>v' + P.version_no + '</td><td>' + esc(res.saved_at) + ' today</td><td>' + esc(P.updated_by || '') + '</td><td>Saved</td><td class="num">' + fmt(P.grand.total_sell) + '</td><td><a href="?v=' + P.version_no + '">view</a></td></tr>');
      }).catch(() => { saving = false; $('btn-save').textContent = 'Save'; toast('save failed'); });
  }
  function saveMeta() {
    if (!WRITE) return;
    clearTimeout(metaT);
    metaT = setTimeout(() => {
      fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({action: 'meta', id: P.id, title: $('meta-title').value, client_name: $('meta-client').value, notes: $('meta-notes').value, status: $('meta-status').value, project_number: $('meta-project').value})})
        .then(r => r.json()).then(res => { if (res.ok) { P.version_no = res.version_no; P.title = $('meta-title').value; document.title = (P.title || 'Estimate') + ' · Pace Company Analytics'; $('h1-title').textContent = P.title || P.client_name || ('Estimate ' + P.id); toast('Details saved'); if (res.payload && res.payload.bid !== undefined) { P.bid = res.payload.bid; renderBid(); renderTotals(); } } });
    }, 700);
  }
  function renderBid() {
    const b = P.bid, box = $('bid-card'); if (!box) return;
    if (!b) { box.innerHTML = '<span class="muted">No bid attached.</span>'; return; }
    box.innerHTML = '<div class="bidcard"><div><div class="muted" style="font-size:11px">Bid</div><b>' + esc(b.project_name) + '</b><div class="note">' + esc(b.client_name) + (b.job_number_raw ? ' · job ' + esc(b.job_number_raw) : '') + ' · <span class="tag">' + esc(b.stage) + '</span>' + (b.estimator ? ' · ' + esc(b.estimator) : '') + '</div></div>' +
      '<div title="Budget typed into the Project Portal (cost basis)"><div class="muted" style="font-size:11px">Bid budget</div><b>' + (b.budget == null ? '—' : fmt(b.budget)) + '</b></div><div title="Project Value typed into the Project Portal (price)"><div class="muted" style="font-size:11px">Project value</div><b>' + (b.value == null ? '—' : fmt(b.value)) + '</b></div>' +
      '<div><a class="btn small" href="' + CFG.bid_url.replace('/0/', '/' + b.id + '/') + '">Open bid</a> ' + (WRITE ? '<button class="btn small" id="bid-detach" title="Detach this estimate from the bid">Detach</button>' : '') + '</div></div>';
  }

  // ---- events
  document.addEventListener('change', e => { const t = e.target; if (t.closest('#eb') && (t.dataset.f || t.dataset.h)) onEdit(t); });
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
    if (e.target.closest && e.target.closest('#eb input') && e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
  });
  document.addEventListener('click', e => {
    const rc = e.target.closest('[data-room]'); if (rc) { cur = +rc.dataset.room; PCA.setPref(prefKey + '.room', cur); renderAll(); return; }
    if (e.target.id === 'room-add') { const n = prompt('Room name:', 'Room ' + (P.rooms.length + 1)); if (n === null) return; P.rooms.push({id: nid--, name: n.trim() || ('Room ' + (P.rooms.length + 1)), notes: '', lines: []}); cur = P.rooms.length - 1; renderAll(); setDirty(true); return; }
    if (e.target.id === 'room-dup') { const r = room(); const n = prompt('Name for the copy:', r.name + ' Copy'); if (n === null) return; P.rooms.push({id: nid--, name: n.trim() || (r.name + ' Copy'), notes: r.notes, lines: r.lines.map(l => ({...l, id: nid--, area: n.trim() || l.area}))}); cur = P.rooms.length - 1; renderAll(); setDirty(true); return; }
    if (e.target.id === 'room-del') { const r = room(); if (P.rooms.length === 1) { if (!confirm('This is the only room — clear all its lines?')) return; r.lines = []; } else { if (!confirm('Delete room "' + r.name + '" and its ' + r.lines.length + ' lines?')) return; P.rooms.splice(cur, 1); cur = Math.max(0, cur - 1); } renderAll(); setDirty(true); return; }
    if (e.target.id === 'room-rename') { const r = room(), n = $('room-name').value.trim(); if (!n) return; const old = r.name; r.name = n; r.lines.forEach(l => { if (!l.area || l.area === old) l.area = n; }); renderAll(); setDirty(true); toast('Room renamed to ' + n); return; }
    const act = e.target.closest('[data-act]');
    if (act) { const tr = act.closest('tr'), j = +tr.dataset.j, r = room(), a = act.dataset.act;
      if (a === 'rm') r.lines.splice(j, 1); else if (a === 'dup') r.lines.splice(j + 1, 0, {...r.lines[j], id: nid--, hours: {...(r.lines[j].hours || {})}});
      else if (a === 'up' && j > 0) { [r.lines[j - 1], r.lines[j]] = [r.lines[j], r.lines[j - 1]]; } else if (a === 'down' && j < r.lines.length - 1) { [r.lines[j + 1], r.lines[j]] = [r.lines[j], r.lines[j + 1]]; }
      renderAll(); setDirty(true); return; }
    const chip = e.target.closest('#cols .lchip[data-g]'); if (chip) { cols[chip.dataset.g] = !cols[chip.dataset.g]; PCA.setPref('cols', cols); renderCols(); return; }
    const band = e.target.closest('#eb thead tr.bands th[data-g]'); if (band) { cols[band.dataset.g] = false; PCA.setPref('cols', cols); renderCols(); return; }
    if (e.target.id === 'btn-save') { save(); return; }
    if (e.target.id === 'btn-copy') { fetch(CFG.export_url + '?fmt=txt').then(r => r.text()).then(t => navigator.clipboard.writeText(t).then(() => toast('Estimate copied to clipboard'), () => toast('Clipboard blocked — use Export CSV'))); return; }
    if (e.target.id === 'bid-detach') { fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({action: 'attach_bid', id: P.id, bid_id: null})}).then(r => r.json()).then(res => { if (res.ok) { P = Object.assign(P, {bid: null, bid_id: null, version_no: res.payload.version_no}); renderBid(); renderTotals(); toast('Bid detached'); } }); return; }
    const bs = e.target.closest('[data-bid]'); if (bs) { fetch(CFG.save_url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()}, body: JSON.stringify({action: 'attach_bid', id: P.id, bid_id: +bs.dataset.bid})}).then(r => r.json()).then(res => { if (res.ok) { P.bid = res.payload.bid; P.bid_id = res.payload.bid_id; P.version_no = res.payload.version_no; if (!$('meta-client').value && P.bid) $('meta-client').value = P.bid.client_name; renderBid(); renderTotals(); $('bid-sugg').hidden = true; $('bid-search').value = ''; toast('Bid attached'); } }); return; }
  });
  document.addEventListener('dblclick', e => { const rc = e.target.closest('#rooms .room'); if (rc && WRITE) { const r = P.rooms[+rc.dataset.room]; const n = prompt('Rename room:', r.name); if (n && n.trim()) { const old = r.name; r.name = n.trim(); r.lines.forEach(l => { if (!l.area || l.area === old) l.area = r.name; }); renderAll(); setDirty(true); } } });
  ['meta-title', 'meta-client', 'meta-notes', 'meta-status', 'meta-project'].forEach(id => { const el = $(id); if (el) el.addEventListener('change', saveMeta); });
  if ($('room-notes')) $('room-notes').addEventListener('change', () => { room().notes = $('room-notes').value; setDirty(true); });
  if ($('room-name')) $('room-name').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('room-rename').click(); } });
  const bsrch = $('bid-search');
  if (bsrch) { let bt; bsrch.addEventListener('input', () => { clearTimeout(bt); bt = setTimeout(() => { const v = bsrch.value.trim(); if (!v) { $('bid-sugg').hidden = true; return; }
    fetch(CFG.search_url + '?kind=bids&q=' + encodeURIComponent(v)).then(r => r.json()).then(res => { $('bid-sugg').innerHTML = res.rows.map(b => '<div data-bid="' + b.id + '"><b>' + esc(b.project_name) + '</b> · ' + esc(b.client_name) + ' · <span class="tag">' + esc(b.stage) + '</span> · ' + (b.value == null ? '—' : fmt0(b.value)) + (b.job ? ' · ' + esc(b.job) : '') + '</div>').join('') || '<div class="muted">no bids match</div>'; $('bid-sugg').hidden = false; }); }, 200); });
    bsrch.addEventListener('keydown', e => { if (e.key === 'Escape') { bsrch.value = ''; $('bid-sugg').hidden = true; } }); }
  const q = $('quick'), sg = $('quick-sugg');
  if (q) {
    q.addEventListener('input', () => { clearTimeout(quickT); quickT = setTimeout(() => { const v = q.value.trim(); if (!v) { sg.hidden = true; return; }
      fetch(CFG.search_url + '?' + new URLSearchParams({q: v, per: 8, has_cost: '0'})).then(r => r.json()).then(res => { quickRows = res.rows; quickIdx = quickRows.length ? 0 : -1;
        sg.innerHTML = quickRows.map((r, i) => '<div data-i="' + i + '" class="' + (i === quickIdx ? 'on' : '') + '"><b>' + esc(r.part) + '</b> · ' + esc(r.mfr) + ' · ' + (r.cost == null ? 'N/A' : fmt(r.cost)) + ' · <span class="muted">' + esc(r.desc).slice(0, 60) + '</span> <span class="match ' + r.score_class + '">' + esc(r.score_label) + '</span></div>').join('') || '<div class="muted">no match — press Enter to add it as a typed line</div>'; sg.hidden = false; }); }, 160); });
    q.addEventListener('keydown', e => {
      if (e.key === 'Escape') { q.value = ''; sg.hidden = true; }
      else if (e.key === 'ArrowDown') { quickIdx = Math.min(quickRows.length - 1, quickIdx + 1); paint(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { quickIdx = Math.max(0, quickIdx - 1); paint(); e.preventDefault(); }
      else if (e.key === 'Enter') { e.preventDefault(); const v = q.value.trim(); if (quickIdx >= 0 && quickRows[quickIdx]) addLine(quickRows[quickIdx]); else if (v) addLine({id: null, mfr: 'Imported', part: v, norm: v.toUpperCase().replace(/[\s\-_.\/]/g, ''), desc: v, cost: 0, msrp: 0, source: '', dk: 0}); q.value = ''; sg.hidden = true; }
    });
    sg.addEventListener('click', e => { const d = e.target.closest('[data-i]'); if (d) { addLine(quickRows[+d.dataset.i]); q.value = ''; sg.hidden = true; } });
    function paint() { sg.querySelectorAll('[data-i]').forEach(d => d.classList.toggle('on', +d.dataset.i === quickIdx)); }
  }
  window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  document.addEventListener('submit', e => { if (e.target.dataset.confirm && !confirm(e.target.dataset.confirm)) e.preventDefault(); });

  // ---- boot
  $('cols').innerHTML = '<span class="dim">Columns:</span>' + GROUPS.map(g => '<span class="lchip" data-g="' + g[0] + '" title="' + esc(g[2]) + '">' + g[1] + '</span>').join('') + '<span class="muted" style="margin-left:8px">click a band heading to collapse it · your choice is remembered</span>';
  renderBid(); renderAll(); setDirty(false);
})();
