/* Behaviour of the shared job table (dashboard/job_table.py + _job_table.html): the WIP by Job page and the customer
   page's Projects card. PCAJobTable.init(id, {fit, hidden, legacyKey, sets}):
     - keeps the frozen columns aligned and, with fit, sizes the scroll area to the viewport (the WIP page);
     - column groups (Customer and the bands) toggle from the chips above the table or a click on a band heading,
       remembered per page through PCA.pref (the WIP page migrates its older localStorage key once);
     - with sets, the Active / Completed chips show or hide the rows of each set and switch the footer total to the
       matching one (both = all jobs), remembered the same way;
     - rich hover / click tips (the Hrs % breakdown) and click-to-pin definitions inside the table.
   PCA.pref / PCA.setPref come from base.html, which is why the partial defers init to DOMContentLoaded. */
window.PCAJobTable = (function () {
  const roots = [], tables = {};
  let tipEl = null;
  const hideTip = () => { if (tipEl) { tipEl.remove(); tipEl = null; } };
  document.addEventListener('click', e => {
    if (!roots.some(r => r.contains(e.target))) return;
    const t = e.target.closest('th[title], td[title], div[title], span[title], label[title]');
    if (!t || e.target.closest('a, button, input, select, .rich-tip')) { hideTip(); return; }
    const text = t.getAttribute('title');
    if (!text) { hideTip(); return; }
    hideTip();
    tipEl = document.createElement('div'); tipEl.className = 'click-tip'; tipEl.textContent = text; document.body.appendChild(tipEl);
    const r = t.getBoundingClientRect();
    tipEl.style.left = Math.min(r.left, window.innerWidth - 380) + 'px'; tipEl.style.top = (r.bottom + window.scrollY + 6) + 'px';
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });
  // click-to-pin definitions: register a root (the table wrap by default; the WIP page adds the whole document)
  function clickTips(root) { if (root && !roots.includes(root)) roots.push(root); }

  // base.html declares PCA with `const` (a global lexical binding, not a window property), hence typeof
  const hasPref = () => typeof PCA !== 'undefined' && typeof PCA.pref === 'function';
  const pref = (k, d) => hasPref() ? PCA.pref(k, d) : d;
  const setPref = (k, v) => { if (hasPref()) PCA.setPref(k, v); };

  function init(id, opts) {
    opts = opts || {};
    const table = document.getElementById(id), wrap = document.getElementById(id + '-wrap'), chips = document.getElementById(id + '-chips');
    if (!table || !wrap) return;
    // --- geometry: the second frozen column sits behind the first; the column header row sits under the band row
    function fit() {
      if (opts.fit) {
        const top = wrap.getBoundingClientRect().top + window.scrollY;
        wrap.style.maxHeight = Math.max(320, window.innerHeight - top - 16) + 'px';
      }
      const fz1 = wrap.querySelector('tr.cols th.fz1');
      if (fz1) wrap.querySelectorAll('.fz2').forEach(el => { el.style.left = fz1.offsetWidth + 'px'; });
      const bands = wrap.querySelector('thead tr.bands');
      if (bands) { const h = bands.getBoundingClientRect().height; wrap.querySelectorAll('thead tr.cols th').forEach(th => { th.style.top = h + 'px'; }); }
    }
    fit();
    window.addEventListener('resize', fit);
    // --- alternate-row shading over the *visible* job rows (Owner, 2026-09-08: easier to scan horizontally); crew rows and
    // rows hidden by a set toggle or a page filter are skipped so the stripes stay regular whatever is shown
    function restripe() {
      let i = 0;
      // the table's own body rows only — the Hrs % cells carry nested tip tables whose rows must not count
      [...(table.tBodies[0] ? table.tBodies[0].rows : [])].forEach(tr => {
        if (tr.classList.contains('jt-k') || tr.classList.contains('jt-none')) return;
        if (tr.hidden || tr.style.display === 'none' || getComputedStyle(tr).display === 'none') { tr.classList.remove('alt'); return; }
        tr.classList.toggle('alt', (i++ % 2) === 1);
      });
    }
    // --- column groups
    const GROUPS = chips ? [...chips.querySelectorAll('.lchip[data-g]')].map(c => c.dataset.g) : [];
    const colsKey = 'jt.' + id + '.cols', state = {};
    GROUPS.forEach(g => { state[g] = !(opts.hidden || []).includes(g); });
    if (opts.legacyKey) {   // the WIP page's pre-2026-09-08 memory, honoured until the user toggles once
      try { const saved = JSON.parse(localStorage.getItem(opts.legacyKey) || 'null'); if (saved) for (const g of GROUPS) if (typeof saved[g] === 'boolean') state[g] = saved[g]; } catch (e) {}
    }
    const savedCols = pref(colsKey);
    if (savedCols && typeof savedCols === 'object') for (const g of GROUPS) if (typeof savedCols[g] === 'boolean') state[g] = savedCols[g];
    // band cells span the columns under them: when a column inside a band is hidden (the State column), shrink the band;
    // hide a band cell whose columns are all hidden. Crew note cells span the identity columns the same way.
    function fixSpans() {
      const ths = [...table.querySelectorAll('thead tr.cols th')];
      let i = 0;
      table.querySelectorAll('thead tr.bands th[data-span]').forEach(bc => {
        const n = +bc.dataset.span, mine = ths.slice(i, i + n); i += n;
        const vis = mine.filter(th => !GROUPS.some(g => !state[g] && th.classList.contains('cb-' + g))).length;
        bc.hidden = vis === 0;
        if (vis) bc.colSpan = vis;
      });
      table.querySelectorAll('td.jt-note[data-span]').forEach(td => { td.colSpan = Math.max(1, +td.dataset.span - (td.dataset.hasstate && state.state === false ? 1 : 0)); });
    }
    function applyCols(save) {
      for (const g of GROUPS) table.classList.toggle('hide-' + g, !state[g]);
      if (chips) chips.querySelectorAll('.lchip[data-g]').forEach(c => c.classList.toggle('on', !!state[c.dataset.g]));
      fixSpans();
      if (save) setPref(colsKey, state);
      fit();
    }
    if (chips) chips.querySelectorAll('.lchip[data-g]').forEach(c => c.addEventListener('click', () => { state[c.dataset.g] = !state[c.dataset.g]; applyCols(true); }));
    table.querySelectorAll('thead tr.bands th[data-g]').forEach(th => th.addEventListener('click', () => { state[th.dataset.g] = false; applyCols(true); }));
    applyCols(false);
    // --- row sets (Active / Completed) with a footer total per visible combination
    if (opts.sets && chips) {
      const setsKey = 'jt.' + id + '.sets', sstate = {open: true, closed: false};
      const savedSets = pref(setsKey);
      if (savedSets && typeof savedSets === 'object') for (const k of Object.keys(sstate)) if (typeof savedSets[k] === 'boolean') sstate[k] = savedSets[k];
      function applySets(save) {
        for (const k of Object.keys(sstate)) table.classList.toggle('hide-set-' + k, !sstate[k]);
        chips.querySelectorAll('.lchip.set').forEach(c => c.classList.toggle('on', !!sstate[c.dataset.set]));
        const v = sstate.open && sstate.closed ? 'all' : sstate.open ? 'open' : sstate.closed ? 'closed' : 'none';
        table.querySelectorAll('tr.jt-tot').forEach(tr => { tr.hidden = tr.dataset.set !== v; });
        const none = table.querySelector('tr.jt-none'); if (none) none.hidden = v !== 'none';
        if (save) setPref(setsKey, sstate);
        restripe();
        fit();
      }
      chips.querySelectorAll('.lchip.set').forEach(c => c.addEventListener('click', () => { sstate[c.dataset.set] = !sstate[c.dataset.set]; applySets(true); }));
      applySets(false);
    }
    // --- rich hover / click tips (the Hrs % breakdown): the cell carries its own hidden markup in .tip-src
    (function () {
      let tip = null, pinned = false, anchor = null, timer = null;
      const hide = () => { if (tip) { tip.remove(); tip = null; } pinned = false; anchor = null; };
      const show = (el, pin) => {
        const src = el.querySelector('.tip-src');
        if (!src) return;
        if (tip && anchor === el) { pinned = pinned || pin; return; }
        hide();
        tip = document.createElement('div'); tip.className = 'click-tip rich'; tip.innerHTML = src.innerHTML; document.body.appendChild(tip);
        const r = el.getBoundingClientRect(), w = tip.offsetWidth;
        tip.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
        tip.style.top = (r.bottom + window.scrollY + 6) + 'px';
        anchor = el; pinned = pin;
      };
      table.querySelectorAll('.rich-tip').forEach(el => {
        el.addEventListener('mouseenter', () => { clearTimeout(timer); timer = setTimeout(() => { if (!pinned) show(el, false); }, 160); });
        el.addEventListener('mouseleave', () => { clearTimeout(timer); if (!pinned) hide(); });
        el.addEventListener('click', e => { e.stopPropagation(); if (pinned && anchor === el) hide(); else show(el, true); });
      });
      document.addEventListener('click', () => { if (pinned) hide(); });
      document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
    })();
    // --- expandable rows (a job's crew on the project snapshot): a row with data-g toggles the .pay-inv rows that share it
    table.querySelectorAll('tbody tr.jt-g[data-g]').forEach(tr => tr.addEventListener('click', e => {
      if (e.target.closest('a')) return;
      const kids = table.querySelectorAll('tbody tr.jt-k[data-g="' + tr.dataset.g + '"]');
      const open = kids.length && kids[0].classList.contains('pay-hidden');
      kids.forEach(k => k.classList.toggle('pay-hidden', !open));
      const c = tr.querySelector('.pay-caret'); if (c) c.textContent = open ? '▾' : '▸';
    }));
    clickTips(wrap);
    restripe();
    tables[id] = {fit, restripe};
    return tables[id];
  }
  // pages that hide rows themselves (the snapshot's Worked / All filter) call this afterwards to keep the stripes regular
  function restripe(id) { if (tables[id]) tables[id].restripe(); }
  return { init, clickTips, restripe };
})();
