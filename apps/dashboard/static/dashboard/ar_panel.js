/* Shared AR document behaviour (docs/ar_page_plan.md §3b–3c), used by the Accounts Receivable page and the
   "Outstanding invoices" card on a customer page:
     PCAArPanel.attach(table, docUrl)      — click a <tr class="ar-d" data-ref data-type data-cust> to open a detail
                                             panel (<tr class="ar-x">) underneath it: what was billed, payments so far,
                                             job / order / contact context; fetched once from /finance/ar/doc/ and cached.
     PCAArPanel.sortable(table, headClass) — header sort on <th class="ar-sort" data-c>: rows carrying headClass are the
                                             sortable blocks and the rows after each (documents, panels) travel with it.
                                             Cells sort by data-v when present, signed money by magnitude first click.
   Links inside rows never toggle anything (rows stop the click when it lands on an <a>). */
window.PCAArPanel = (function () {
  const PROJ = '/projects/', PEOPLE = '/people/', ORDER = '/sales/010/orders/';
  const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const money = (v, c) => v == null ? '—' : (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: c ? 2 : 0, maximumFractionDigits: c ? 2 : 0 });
  const pct = v => v == null ? '—' : Math.round(v * 100) + '%';
  const cache = {};

  function panel(r) {
    const lines = r.lines.filter(l => !l.tax), tax = r.lines.filter(l => l.tax);
    let a = '<div class="ar-panel"><div><h4>What was billed</h4>';
    if (lines.length) {
      a += '<table class="data dense"><thead><tr><th>Line</th><th>Job · task</th><th class="num">Qty × price</th><th class="num">Amount</th></tr></thead><tbody>';
      lines.forEach(l => { a += '<tr><td>' + esc(l.desc || l.item || '—') + (l.item && l.desc ? ' <span class="muted mono">' + esc(l.item) + '</span>' : '') + '</td><td class="muted">' + (l.project ? '<span class="mono">' + esc(l.project) + '</span>' + (l.task ? ' · ' + esc(l.task) : '') : '') + '</td><td class="num muted">' + (l.qty ? l.qty.toLocaleString() + ' × ' + money(l.price, true) : '') + '</td><td class="num">' + money(l.amt, true) + '</td></tr>'; });
      if (tax.length) a += '<tr><td class="muted" colspan="3">sales tax</td><td class="num muted">' + money(tax.reduce((s, l) => s + (l.amt || 0), 0), true) + '</td></tr>';
      a += '</tbody><tfoot><tr><td colspan="3">Original</td><td class="num">' + money(r.orig, true) + '</td></tr></tfoot></table>';
    } else a += '<div class="empty">no invoice lines on record</div>';
    a += '</div><div><h4>Payments on this document</h4>';
    if (r.apps.length) {
      let run = r.orig || 0;
      a += '<table class="data dense"><thead><tr><th>Applied</th><th>Payment</th><th class="num">Amount</th><th class="num">Left</th></tr></thead><tbody>';
      r.apps.forEach(p => { run -= (p.amt || 0) + (p.disc || 0); a += '<tr><td>' + esc(p.d) + '</td><td class="mono">' + esc(p.ref) + (p.disc ? ' <span class="muted">disc ' + money(p.disc, true) + '</span>' : '') + '</td><td class="num">' + money(p.amt, true) + '</td><td class="num muted">' + money(run, true) + '</td></tr>'; });
      a += '</tbody></table>';
    } else a += '<div class="empty">no payment applied yet' + (r.days > 0 ? ' · ' + r.days + ' days past due' : '') + '</div>';
    a += '<div class="muted" style="margin-top:6px;font-size:12px">Balance <b>' + money(r.bal, true) + '</b> · invoiced ' + esc(r.doc_date) + ' · due ' + esc(r.due) + (r.terms ? ' · terms ' + esc(r.terms) : '') + (r.po ? ' · PO ' + esc(r.po) : '') + (r.per_post ? ' · period ' + esc(r.per_post) : '') + (r.sp ? ' · ' + esc(r.sp) : '') + '</div>';
    a += '</div><div><h4>Context</h4>';
    if (r.project) {
      const p = r.project;
      a += '<div><a href="' + PROJ + encodeURIComponent(p.cpn) + '/"><b>' + esc(p.disp) + '</b></a> ' + esc(p.title) + (p.div ? ' <span class="ar-chip">' + esc(p.div) + '</span>' : '') + (p.state ? ' <span class="ar-chip">' + esc(p.state.replace(/_/g, ' ')) + '</span>' : '') + '</div>';
      a += '<div class="muted" style="font-size:12px;margin-top:4px">' + (p.pm ? 'PM <a href="' + PEOPLE + encodeURIComponent(p.pm_key) + '/">' + esc(p.pm) + '</a> · ' : '') + 'contract ' + money(p.cv) + ' · billed ' + money(p.billed) + (p.cv ? ' (' + pct(p.billed / p.cv) + ')' : '') + (p.earned != null ? ' · earned ' + money(p.earned) : '') + (p.pct != null ? ' · PTT ' + pct(p.pct) + ' complete' : '') + (p.close ? ' · closed ' + esc(p.close) : '') + ' · <a href="' + PROJ + encodeURIComponent(p.cpn) + '/#revisions">job page</a></div>';
      if (p.siblings.length) { a += '<div class="muted" style="font-size:12px;margin-top:6px">Other open documents on this job:</div><table class="data dense"><tbody>'; p.siblings.forEach(s => { a += '<tr><td class="mono">' + esc(s.ref) + (s.type !== 'IN' ? ' ' + esc(s.type) : '') + '</td><td class="muted">' + esc(s.date) + ' · due ' + esc(s.due) + '</td><td class="num ' + (s.days > 90 ? 'neg' : '') + '">' + (s.days > 0 ? s.days + 'd' : '') + '</td><td class="num">' + money(s.bal, true) + '</td></tr>'; }); a += '</tbody></table>'; }
    }
    if (r.order) {
      const o = r.order;
      a += '<div style="margin-top:6px">' + (o.cnet ? '<a href="' + ORDER + encodeURIComponent(o.cnet) + '/"><b>Order ' + esc(o.nbr) + '</b></a> · CNET #' + esc(o.cnet) : '<b>Order ' + esc(o.nbr) + '</b>') + (o.so_type ? ' <span class="ar-chip">' + esc(o.so_type) + '</span>' : '') + '</div>';
      if (o.ship_name || o.ship_city) a += '<div class="muted" style="font-size:12px">ships to ' + esc(o.ship_name || '') + (o.ship_city ? ' · ' + esc(o.ship_city) + (o.ship_state ? ', ' + esc(o.ship_state) : '') : '') + (o.cust_po ? ' · PO ' + esc(o.cust_po) : '') + '</div>';
    }
    if (!r.project && !r.order) a += '<div class="muted" style="font-size:12px">No job or order on this document' + (r.desc ? ' — "' + esc(r.desc) + '"' : '') + '.</div>';
    if (r.contact) {
      const c = r.contact;
      a += '<h4 style="margin-top:10px">Who to call</h4><div class="muted" style="font-size:12px">' + esc(c.bill_name || c.name || r.customer) + (c.bill_attn || c.attn ? ' · attn ' + esc(c.bill_attn || c.attn) : '') + (c.bill_phone || c.phone ? ' · ' + esc(c.bill_phone || c.phone) : '') + (c.email ? ' · <a href="mailto:' + esc(c.email) + '">' + esc(c.email) + '</a>' : '') + (c.terms ? ' · terms ' + esc(c.terms) : '') + (c.stmt_cycle ? ' · statements ' + esc(c.stmt_cycle) : '') + (c.credit_limit ? ' · credit limit ' + money(c.credit_limit) : '') + (c.setup_date ? ' · customer since ' + esc(c.setup_date).slice(0, 4) : '') + '</div>';
    } else a += '<div class="muted" style="font-size:12px;margin-top:10px">No contact on the SL customer master.</div>';
    return a + '</div></div>';
  }

  function attach(table, docUrl) {
    const ncols = table.tHead.rows[0].cells.length;
    table.querySelectorAll('tr.ar-d').forEach(tr => tr.addEventListener('click', e => {
      if (e.target.closest('a')) return;
      let x = tr.nextElementSibling;
      const caret = tr.querySelector('.ar-dcaret');
      const setCaret = open => { if (caret) caret.textContent = open ? '▾' : '▸'; };
      if (x && x.classList.contains('ar-x')) { x.hidden = !x.hidden; setCaret(!x.hidden); return; }
      x = document.createElement('tr'); x.className = 'ar-x'; x.innerHTML = '<td colspan="' + ncols + '"><div class="empty">loading…</div></td>';
      tr.after(x); setCaret(true);
      const key = tr.dataset.ref + '|' + tr.dataset.type + '|' + tr.dataset.cust;
      const render = res => { x.firstElementChild.innerHTML = panel(res); };
      if (cache[key]) return render(cache[key]);
      fetch(docUrl + '?ref=' + encodeURIComponent(tr.dataset.ref) + '&type=' + encodeURIComponent(tr.dataset.type) + '&cust=' + encodeURIComponent(tr.dataset.cust))
        .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(res => { cache[key] = res; render(res); })
        .catch(() => { x.firstElementChild.innerHTML = '<div class="empty">failed to load</div>'; });
    }));
  }

  function sortable(table, headClass) {
    table.querySelectorAll('th.ar-sort').forEach(th => th.addEventListener('click', () => {
      const col = +th.dataset.c, tbody = table.tBodies[0];
      const dir = table.dataset.sort === col + ':desc' ? 'asc' : 'desc'; table.dataset.sort = col + ':' + dir;
      table.querySelectorAll('th.ar-sort').forEach(h => h.removeAttribute('aria-sort'));
      th.setAttribute('aria-sort', dir === 'desc' ? 'descending' : 'ascending');
      const blocks = []; let cur = null;
      [...tbody.rows].forEach(tr => { if (tr.classList.contains(headClass)) { cur = { g: tr, kids: [] }; blocks.push(cur); } else if (cur) cur.kids.push(tr); });
      const val = tr => { const td = tr.cells[col]; if (!td) return null; const v = td.dataset.v !== undefined ? td.dataset.v : td.textContent.trim(); if (v === '' || v === '—') return null; const n = Number(v); return isNaN(n) ? v.toLowerCase() : n; };   // Number(), not parseFloat(): '2026-08-30' must sort as a date string, not as 2026
      blocks.sort((a, b) => { const va = val(a.g), vb = val(b.g); if (va === null && vb === null) return 0; if (va === null) return 1; if (vb === null) return -1;
        if (typeof va === 'number' && typeof vb === 'number') return dir === 'desc' ? Math.abs(vb) - Math.abs(va) : Math.abs(va) - Math.abs(vb);
        return dir === 'desc' ? String(vb).localeCompare(String(va)) : String(va).localeCompare(String(vb)); });
      blocks.forEach(b => { tbody.appendChild(b.g); b.kids.forEach(k => tbody.appendChild(k)); });
    }));
  }

  return { panel, attach, sortable, money, esc, pct };
})();
