// Project Map — 3D towers for projects (and 010 hardware-sales sites) on a MapLibre basemap (docs/08_project_map.md).
// Data: /map/data/?w=<window>  (apps.analytics.project_map). Everything else happens here.
(function () {
  'use strict';
  const CFG = JSON.parse(document.getElementById('pmap-cfg').textContent);
  const $ = id => document.getElementById(id);
  const STYLE_URL = k => 'https://tiles.openfreemap.org/styles/' + (k === 'liberty' ? 'liberty' : k === 'dark' ? 'dark' : 'positron');
  const CHICAGO = {center: [-87.75, 41.86], zoom: 9.6, pitch: 55, bearing: -12};
  const LOOP = {center: [-87.63, 41.882], zoom: 13.6, pitch: 62, bearing: -20};
  const G = window.PCAMapGeometry;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const duration = ms => reducedMotion ? 0 : ms;
  const fmt$ = v => v == null ? '—' : (Math.abs(v) >= 1e6 ? '$' + (v / 1e6).toFixed(2) + 'M' : Math.abs(v) >= 1e3 ? '$' + Math.round(v / 1e3) + 'k' : '$' + Math.round(v));
  const fmtH = v => v == null ? '—' : (Math.round(v * 10) / 10).toLocaleString('en-US') + ' h';
  const pct0 = v => v == null ? '—' : Math.round(v * 100) + '%';
  const pct1 = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  const pts = v => v == null ? '' : (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + ' pts';
  const sgn = v => v == null ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '';
  const plural = (n, w) => n + ' ' + w + (n === 1 ? '' : 's');
  const esc = t => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  const STATE_LABEL = {in_progress: 'In progress', awarded_not_started: 'Awarded / not started', field_complete: 'Field complete', dormant: 'Dormant',
    closed_stabilizing: 'Closed / stabilizing', closed_stabilized: 'Closed', canceled: 'Canceled', template: 'Template', unknown: 'Unknown'};
  const STATE_ORDER = ['in_progress', 'awarded_not_started', 'field_complete', 'dormant', 'closed_stabilizing', 'closed_stabilized', 'unknown'];
  const DEFAULT_STATES = ['in_progress'];          // first load shows only work that is under way
  const STATE_COLOR = {in_progress: '#1f5eff', awarded_not_started: '#6a3ff0', field_complete: '#b7791f', dormant: '#e07a1f', closed_stabilizing: '#2c7a4b',
    closed_stabilized: '#8fa0b8', canceled: '#999', template: '#999', unknown: '#999'};
  const MODE_LABEL = {installation: 'Installation / project', tm_ticket: 'T&M ticket', tm_service: 'T&M service blanket', service_agreement: 'Service agreement',
    joc: 'JOC work', warranty: 'Warranty', internal: 'Internal', canceled: 'Canceled', template: 'Template', unknown: 'Unknown', sale: '010 hardware sale'};
  const PALETTE = ['#1f5eff', '#178a52', '#b7791f', '#c62828', '#6a3ff0', '#0e9aa7', '#8d6e63', '#546e7a', '#e91e63', '#3949ab', '#00897b', '#f4511e'];
  // fixed division colours so a tower never changes colour when the window or filters change
  const DIV_COLOR = {'000': '#546e7a', '010': '#e91e63', '020': '#0e9aa7', '025': '#8d6e63', '030': '#6a3ff0', '040': '#b7791f', '050': '#c62828', '060': '#f4511e', '070': '#1f5eff', '080': '#178a52'};
  const SALE_COLOR = DIV_COLOR['010'];
  const Q_LABEL = {exact: 'street address, exact match', approx: 'street address, approximate match', city: 'city centroid only', manual: 'pinned by hand'};
  const Q_COLOR = {exact: '#178a52', approx: '#b7791f', city: '#c62828', manual: '#1f5eff'};
  const SRC_LABEL = {site: 'sales-order ship-to', customer: 'customer address', city: 'customer city', manual: 'set by hand', ship: 'CNET ship-to'};
  const isSale = p => p.kind === 'sale';
  const isClosed = p => !!p.cl || /^closed/.test(p.s || '');

  // ------------------------------------------------------------------ state
  const freshFilters = () => ({hideDiv: new Set(), mode: '', sol: '', sec: '', pm: '', mincv: 0, q: '', crew: false, states: new Set(DEFAULT_STATES), person: '', onlyDiv: ''});
  const S = {
    data: null, map: null, styleKey: 'positron', ready: false,
    f: freshFilters(),
    metric: 'cv', color: 'd',
    tl: {on: false, idx: 0, timer: null}, selected: null, visible: [], placed: new Map(), catColors: {}, divs: [],
    hoverPopup: null, hovered: null, groups: new Map(), footprints: new Map(), loadId: 0,
    listLimit: 40, inView: typeof PCA !== 'undefined' ? PCA.pref('map-in-view', false) : false,
    listSort: typeof PCA !== 'undefined' ? PCA.pref('map-sort', 'cv') : 'cv', tab: 'sites',
  };

  // ------------------------------------------------------------------ url state (mirrored into the sidebar's filter memory)
  function readUrl() {
    const q = new URLSearchParams(location.search);
    if (q.get('hidediv')) S.f.hideDiv = new Set(q.get('hidediv').split(',').filter(Boolean));
    if (q.get('div')) S.f.onlyDiv = q.get('div');      // pre-legend URLs: a single division → resolved once the data is here
    ['mode', 'sol', 'sec', 'pm', 'q', 'person'].forEach(k => { if (q.get(k)) S.f[k] = q.get(k); });
    if (q.get('mincv')) S.f.mincv = +q.get('mincv');
    if (q.get('crew') === '1') S.f.crew = true;
    if (q.has('states')) S.f.states = new Set(q.get('states').split(',').filter(Boolean));
    if (q.get('metric')) S.metric = q.get('metric');
    if (q.get('color')) S.color = q.get('color');
    if (['positron','dark','liberty'].includes(q.get('style'))) S.styleKey = q.get('style');
  }
  function writeUrl() {
    const q = new URLSearchParams();
    q.set('w', CFG.window);
    if (S.f.hideDiv.size) q.set('hidediv', [...S.f.hideDiv].sort().join(','));
    ['mode', 'sol', 'sec', 'pm', 'q', 'person'].forEach(k => { if (S.f[k]) q.set(k, S.f[k]); });
    if (S.f.mincv) q.set('mincv', S.f.mincv);
    if (S.f.crew) q.set('crew', '1');
    q.set('states', [...S.f.states].join(','));
    if (S.metric !== 'cv') q.set('metric', S.metric);
    if (S.color !== 'd') q.set('color', S.color);
    if (S.styleKey !== 'positron') q.set('style', S.styleKey);
    history.replaceState(null, '', location.pathname + '?' + q.toString());
    if (typeof PCA !== 'undefined' && PCA.rememberFilters) PCA.rememberFilters();   // so the sidebar's "Project Map" link reopens this exact view
  }

  // ------------------------------------------------------------------ data
  async function load() {
    const id = ++S.loadId, requestedWindow=CFG.window;
    if (S.request) S.request.abort();
    S.request = new AbortController();
    if (S.data) tlStop();
    $('pmap-stats').innerHTML = '<span class="pmap-loading">Loading your sites…</span>';
    $('pmap').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(CFG.data_url + '?w=' + encodeURIComponent(CFG.window), {signal:S.request.signal});
      if (!response.ok) throw new Error('Request failed (' + response.status + ')');
      const d = await response.json();
      if (id !== S.loadId) return;
      S.loadedWindow=requestedWindow; CFG.window=requestedWindow;
      $('f-w').value=['week','month','90d','year','all'].includes(CFG.window)?CFG.window:'custom';
      S.data = d; S.byId = new Map(d.projects.concat(d.unlocated).map(p => [p.id, p]));
      S.tl.on = false; S.tl.idx = d.window.months.length - 1;
      S.footprints.clear();S.buildingSignature=null;S.listLimit = 40;
      buildOptions();
      $('pmap-window-label').textContent = d.window.all_time ? 'All time' : shortDate(d.window.start) + ' – ' + shortDate(d.window.end);
      $('tl-scrub').max = Math.max(0, d.window.months.length - 1); $('tl-scrub').value = S.tl.idx;
      $('tl-label').textContent = windowLabel();
      $('tl-play').disabled = d.window.months.length < 2;
      $('tl-scrub').disabled = d.window.months.length < 2;
      render();
      if (S.selected && S.byId.has(S.selected)) select(S.selected);
      else closeDetail();
      if (S.f.person) selectPerson(S.f.person, true);
    } catch (e) {
      if (e.name === 'AbortError' || id !== S.loadId) return;
      $('pmap-stats').innerHTML = '<span class="pmap-loading">Could not load sites. <button class="pmap-text-button" id="map-retry">Retry</button></span>';
      $('map-retry').onclick = () => {CFG.window=requestedWindow;load();};
      if(S.loadedWindow){CFG.window=S.loadedWindow;$('f-w').value=['week','month','90d','year','all'].includes(CFG.window)?CFG.window:'custom';}
    } finally { if (id === S.loadId) $('pmap').removeAttribute('aria-busy'); }
  }
  function shortDate(value) { return new Date(value + 'T12:00:00').toLocaleDateString('en-US', {month:'short', day:'numeric'}); }
  function monthLabel(value) { return new Date(value + '-15T12:00:00').toLocaleDateString('en-US', {month:'short', year:'numeric'}); }
  function windowLabel() { return ({week:'This week', month:'This month', '90d':'Last 90 days', year:'This year', all:'All time'})[CFG.window] || 'Custom dates'; }

  function buildOptions() {
    const d = S.data, ps = d.projects.concat(d.unlocated), projOnly = ps.filter(p => !isSale(p));
    const count = (key, label, list) => {
      const m = new Map();
      (list || ps).forEach(p => { const k = p[key]; if (k) m.set(k, (m.get(k) || 0) + 1); });
      return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => ({k, n, label: label ? label(k) : k}));
    };
    const fill = (id, opts, cur) => {
      const sel = $(id); const first = sel.options[0].outerHTML;
      sel.innerHTML = first + opts.map(o => '<option value="' + esc(o.k) + '">' + esc(o.label) + ' (' + o.n + ')</option>').join('');
      sel.value = cur || '';
      if (sel.value !== (cur || '')) sel.value = '';
    };
    // divisions, ascending; 010 counts sites, the others count projects
    // ascending by code; 000 (admin / overhead) is not an operating division and goes last
    S.divs = count('d').map(o => ({k: o.k, n: o.n, name: (d.divisions || {})[o.k] || ''})).sort((a, b) => (a.k === '000') - (b.k === '000') || a.k.localeCompare(b.k));
    if (S.f.onlyDiv) { S.f.hideDiv = new Set(S.divs.map(x => x.k).filter(k => k !== S.f.onlyDiv)); S.f.onlyDiv = ''; }
    fill('f-mode', count('m', k => MODE_LABEL[k] || k), S.f.mode);
    fill('f-sol', count('sol', k => k.replace(/_/g, ' ')), S.f.sol);
    fill('f-sec', count('sec'), S.f.sec);
    const pms = new Map(); projOnly.forEach(p => { if (p.pmk) pms.set(p.pmk, p.pm); });
    fill('f-pm', [...pms.entries()].map(([k, n]) => ({k, label: n, n: projOnly.filter(p => p.pmk === k).length})).sort((a, b) => b.n - a.n), S.f.pm);
    $('f-mincv').value = String(S.f.mincv);
    $('f-q').value = S.f.q; $('f-crew').checked = S.f.crew;
    if (![...$('f-metric').options].some(o => o.value === S.metric)) S.metric = 'cv';
    if (![...$('f-color').options].some(o => o.value === S.color)) S.color = 'd';
    $('f-metric').value = S.metric; $('f-color').value = S.color;
    const st = $('f-states'); st.innerHTML = '';
    const present = new Set(projOnly.map(p => p.s));
    STATE_ORDER.filter(k => present.has(k)).forEach(k => {
      const n = projOnly.filter(p => p.s === k).length;
      const el = document.createElement('button'); el.type = 'button'; el.setAttribute('aria-pressed', S.f.states.has(k)); el.className = 'chip' + (S.f.states.has(k) ? ' on' : ''); el.dataset.state = k;
      el.textContent = (STATE_LABEL[k] || k) + ' ' + n; el.title = 'Toggle ' + (STATE_LABEL[k] || k) + (d.sales ? ' (010 sales sites are not projects — the 010 chip in the legend shows or hides them)' : '');
      st.appendChild(el);
    });
    renderPeople();
    const ul = $('pmap-unloc'); $('pmap-unloc-n').textContent = d.unlocated_total ? d.unlocated_total + (d.unlocated_total > d.unlocated.length ? ' (top ' + d.unlocated.length + ')' : '') : '';
    ul.innerHTML = (d.unlocated.length ? d.unlocated.slice(0, 120).map(p => '<div class="row" role="button" tabindex="0" data-open="' + esc(p.id) + '" title="' + esc(p.t) + '"><span class="t"><span class="mono">' + esc(p.n) + '</span> ' + esc(p.t) + '</span><span class="n">' + fmt$(p.cv) + '</span></div>').join('')
      : '<div class="empty">Every project in the window is on the map.</div>')
      + (d.sales_unlocated ? '<div class="note" style="padding:6px 8px">' + plural(d.sales_unlocated, '010 order') + ' in the window have no usable ship-to or customer address.</div>' : '');
  }

  function renderPeople() {
    const d = S.data, q = ($('p-q').value || '').trim().toLowerCase();
    const list = d.people.filter(p => !q || p.n.toLowerCase().includes(q)).slice(0, 80);
    $('pmap-people-n').textContent = d.people.length ? d.people.length + ' people · ' + fmtH(d.people.reduce((s, p) => s + p.h, 0)) : 'none';
    $('pmap-people').innerHTML = list.length ? list.map(p => '<div role="button" tabindex="0" class="row' + (S.f.person === p.k ? ' on' : '') + '" data-person="' + esc(p.k) + '" title="' + esc(p.n) + ' · ' + plural(p.p.length, 'project') + ' — click to show only their sites and route">'
      + '<span class="t">' + esc(p.n) + (p.cls ? ' <span class="pmap-tag">' + esc(p.cls.replace(/_/g, ' ')) + '</span>' : '') + '</span>'
      + '<span class="n">' + (p.p.length ? p.p.length + ' · ' : '') + fmtH(p.h) + (CFG.people ? '<a class="go" href="/people/' + esc(p.k) + '/" data-go title="Open ' + esc(p.n) + '’s person page">↗</a>' : '') + '</span></div>').join('')
      : '<div class="empty">No hours in this window.</div>';
    $('p-clear').hidden = !S.f.person;
  }

  // ------------------------------------------------------------------ filtering + placement
  function passes(p) {
    const f = S.f, sale = isSale(p);
    if (f.hideDiv.has(p.d)) return false;
    if (!sale && !f.states.has(p.s)) return false;          // lifecycle chips are about projects; 010 sites follow the 010 chip
    if (f.mode && p.m !== f.mode) return false;
    if (f.sol && p.sol !== f.sol) return false;
    if (f.sec && p.sec !== f.sec) return false;
    if (f.pm && p.pmk !== f.pm) return false;
    if (f.mincv && (p.cv || 0) < f.mincv) return false;
    if (f.crew && !(p.h > 0)) return false;
    if (f.person) { const per = S.data.people.find(x => x.k === f.person); if (!per || !per.p.some(x => x[0] === p.id)) return false; }
    if (f.q) { const q = f.q.toLowerCase(); if (!((p.n || '').toLowerCase().includes(q) || (p.t || '').toLowerCase().includes(q) || (p.c || '').toLowerCase().includes(q) || (p.addr || '').toLowerCase().includes(q))) return false; }
    return true;
  }
  function metricOf(p) {
    if (S.tl.on) return cumMonth(p, S.tl.idx);
    switch (S.metric) { case 'h': return p.h || 0; case 'cost': return p.cost || 0; case 'hc': return p.hc || 0; case 'bill': return p.bill || 0; default: return p.cv || 0; }
  }
  function cumMonth(p, idx) {      // hours accumulated so far for projects; dollars booked so far for 010 sites
    const months = S.data.window.months; let s = 0;
    for (let i = 0; i <= idx; i++) s += (p.hm && p.hm[months[i]]) || 0;
    return s;
  }
  function monthVal(p, idx) { const m = S.data.window.months[idx]; return (p.hm && p.hm[m]) || 0; }
  function monthHours(p, idx) { return isSale(p) ? 0 : monthVal(p, idx); }
  function quantile(arr, q) { if (!arr.length) return 1; const a = arr.slice().sort((x, y) => x - y); return a[Math.min(a.length - 1, Math.floor(q * (a.length - 1)))] || 1; }

  function colorOf(p, ctx) {
    switch (S.color) {
      case 's': return isSale(p) ? SALE_COLOR : (STATE_COLOR[p.s] || '#999');
      case 'sol': return catColor('sol', p.sol || 'unknown');
      case 'heat': { const v = S.tl.on ? monthHours(p, S.tl.idx) : (p.h || 0); if (!v) return '#c9d1dc'; const t = Math.min(1, Math.sqrt(v / ctx.hmax)); return heat(t); }
      case 'q': return Q_COLOR[p.src === 'manual' ? 'manual' : p.q] || '#999';
      case 'gpp': { if (p.gpp == null) return '#c9d1dc'; const t = Math.max(0, Math.min(1, (p.gpp + 0.2) / 0.6)); return diverge(t); }
      default: return divColor(p.d);
    }
  }
  function divColor(code) { return DIV_COLOR[code] || catColor('d', code); }
  function catColor(kind, key) {
    const m = S.catColors[kind] || (S.catColors[kind] = new Map());
    if (!m.has(key)) m.set(key, PALETTE[m.size % PALETTE.length]);
    return m.get(key);
  }
  const lerp = (a, b, t) => a + (b - a) * t;
  function rgb(c) { return c.startsWith('rgb') ? c.match(/[\d.]+/g).slice(0,3).map(Number) : [parseInt(c.slice(1,3),16),parseInt(c.slice(3,5),16),parseInt(c.slice(5,7),16)]; }
  function mix(a, b, t) { const x = rgb(a), y = rgb(b); return 'rgb(' + Math.round(lerp(x[0], y[0], t)) + ',' + Math.round(lerp(x[1], y[1], t)) + ',' + Math.round(lerp(x[2], y[2], t)) + ')'; }
  const heat = t => t < 0.5 ? mix('#ffd166', '#f4511e', t * 2) : mix('#f4511e', '#8b0000', (t - 0.5) * 2);
  const diverge = t => t < 0.5 ? mix('#c62828', '#e9edf2', t * 2) : mix('#e9edf2', '#178a52', (t - 0.5) * 2);

  const fc = features => ({type:'FeatureCollection', features});
  const feature = (geometry, properties) => ({type:'Feature', geometry, properties});
  function timelineActive(p) {
    if (!S.tl.on) return true;
    if (isSale(p)) return cumMonth(p, S.tl.idx) > 0;
    const month = S.data.window.months[S.tl.idx];
    return cumMonth(p, S.tl.idx) > 0 || p.cr && p.cr <= month + '-31' && (!p.cl || p.cl >= month + '-01');
  }
  function buildFeatures() {
    const all=S.data.projects.filter(passes),vis=all.filter(timelineActive),zoom=S.map.getZoom();S.visible=vis;
    const reference=p=>S.tl.on?cumMonth(p,S.data.window.months.length-1):metricOf(p);
    const sum=(members,value)=>members.reduce((n,p)=>n+Math.max(0,value(p)||0),0);
    const referenceGroups=G.groupSites(all,c=>S.map.project(c),0);
    const projectReference=referenceGroups.filter(g=>g.members.some(p=>!isSale(p))).map(g=>sum(g.members.filter(p=>!isSale(p)),reference));
    const positiveReference=projectReference.filter(v=>v>0);
    const m95=quantile(positiveReference.length?positiveReference:referenceGroups.map(g=>sum(g.members,reference)).filter(v=>v>0),.95)||1;
    const m95s=S.tl.on?quantile(referenceGroups.map(g=>sum(g.members.filter(isSale),reference)).filter(v=>v>0),.95)||1:m95;
    const ctx={hmax:quantile(vis.map(p=>S.tl.on?monthHours(p,S.tl.idx):p.h||0).filter(v=>v>0),.95)};
    const groups=[],physical=new Map();
    for(const g of G.groupSites(vis,c=>S.map.project(c),0)){
      const building=zoom>=15?S.footprints.get(g.id):null;
      if(building&&physical.has(building.id)){physical.get(building.id).members.push(...g.members);continue;}
      groups.push(g);if(building)physical.set(building.id,g);
    }
    const towers=[],models=[],points=[],buildings=[],seenBuildings=new Set();
    S.groups=new Map(groups.map(g=>[g.id,g]));S.placed.clear();
    for(const g of groups){
      g.total=sum(g.members,p=>p.cv);g.value=sum(g.members,metricOf);g.hours=sum(g.members,p=>S.tl.on?monthHours(p,S.tl.idx):p.h);
      const colors=new Map();g.members.forEach(p=>{const c=colorOf(p,ctx);colors.set(c,(colors.get(c)||0)+Math.max(1,reference(p)));});
      g.color=[...colors].sort((a,b)=>b[1]-a[1])[0][0];
      const relative=sum(g.members,p=>metricOf(p)/(isSale(p)?m95s:m95));
      const size=G.towerDimensions(relative,1,zoom),footprint=S.footprints.get(g.id);
      let geometry,side=size.side,c=g.c,streetH=size.h/G.towerScale(zoom);
      if(footprint&&zoom>=15){
        geometry=footprint.geometry;const bounds=footprint.bbox||G.bounds(geometry);
        c=[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2];
        side=Math.max(1,G.meters([bounds[0],c[1]],[bounds[2],c[1]]),G.meters([c[0],bounds[1]],[c[0],bounds[3]]));
        streetH=Math.max(streetH,Number(footprint.properties.render_height)+24);
      }else geometry=G.landFootprint(c,side,S.water||[],window.polygonClipping);
      const h=streetH*G.towerScale(zoom),selected=g.members.some(p=>p.id===S.selected);
      const model={id:g.id,c,side,h,streetH,ratio:size.ratio,color:g.color,group:g.id,count:g.members.length,geometry,mapped:!!footprint&&zoom>=15};
      models.push(model);
      for(const p of g.members)S.placed.set(p.id,{...model,anchor:[p.lng,p.lat],building:!!footprint});
      towers.push(feature(geometry,{id:g.id,pid:g.members.length===1?g.id:'',h,base:0,color:g.color}));
      const props={id:g.id,color:g.color,count:g.members.length,sel:selected?1:0,radius:3,q:g.members[0].q};
      points.push(feature({type:'Point',coordinates:g.c},props));
      if(footprint&&!seenBuildings.has(footprint.id)){
        seenBuildings.add(footprint.id);
        buildings.push(feature(footprint.geometry,{...props,h:Number(footprint.properties.render_height)+.7,base:Math.max(0,Number(footprint.properties.render_min_height)||0)}));
      }
    }
    return {towers:fc(towers),models,points:fc(points),buildings:fc(buildings),ctx};
  }

  // ------------------------------------------------------------------ map
  function initMap() {
    try {
      S.map = new maplibregl.Map({container:'map', style:STYLE_URL(S.styleKey), ...CHICAGO,
        antialias:true, maxPitch:70, attributionControl:{compact:true}});
    } catch (error) {
      $('map-error').hidden = false; $('map-error').textContent = 'The 3D map could not start. Try a browser with WebGL enabled.'; return;
    }
    S.map.addControl(new maplibregl.NavigationControl({visualizePitch:true}), 'top-right');
    S.map.addControl(new maplibregl.ScaleControl({unit:'imperial'}), 'bottom-right');
    S.hoverPopup = new maplibregl.Popup({closeButton:false, closeOnClick:false, className:'pmap-popup', maxWidth:'310px', offset:18});
    S.map.on('style.load', () => { addLayers(); S.ready = true; $('map-error').hidden = true; if(!S.cameraInitialized){S.cameraInitialized=true;S.map.easeTo({...CHICAGO,offset:[$('pmap').classList.contains('explorer-minimized')?0:130,-30],duration:0});} render(); });
    S.map.on('moveend', () => { if (S.ready) { render(true); renderSites(); } });
    S.map.on('idle',()=>{const water=refreshWater(),buildings=refreshBuildings();if(water||buildings)render(true);});
    S.map.getCanvas().addEventListener('pointerdown',()=>stopOrbit());
    S.map.getCanvas().addEventListener('wheel',()=>stopOrbit(),{passive:true});
    S.map.on('pitchend', () => { $('map-perspective').textContent = S.map.getPitch() > 5 ? '2D' : '3D'; $('map-perspective').setAttribute('aria-label', S.map.getPitch() > 5 ? 'Switch to overhead view' : 'Switch to 3D view'); });
    S.map.on('error', e => {
      if (!S.ready) { $('map-error').hidden = false; $('map-error').textContent = 'Map tiles are unavailable. Your site list is still available; reload to retry the map.'; }
    });
    let hoverFrame = null;
    S.map.on('mousemove', e => {
      if (hoverFrame) cancelAnimationFrame(hoverFrame);
      hoverFrame = requestAnimationFrame(() => {
        if (S.map.isMoving()) return;
        const hit = hitAt(e.point); hoverGroup(hit ? hit.id : null, e.lngLat, hit?.pid);
      });
    });
    S.map.getCanvas().addEventListener('mouseleave', () => hoverGroup(null));
    S.map.on('movestart', () => hoverGroup(null));
    S.map.on('click', e => {
      const hit = hitAt(e.point, 18);
      if (!hit) { closeDetail(); return; }
      const group = S.groups.get(hit.id); if (!group) return;
      if (hit.pid) select(hit.pid);
      else if (group.members.length === 1) select(group.id);
      else openGroup(group);
    });
    const observer = new ResizeObserver(() => S.map.resize()); observer.observe($('pmap'));
    setExplorer(typeof PCA !== 'undefined' ? PCA.pref('map-explorer-minimized', window.innerWidth < 900) : window.innerWidth < 900, false);
    updateThemeButton();
  }
  function hitAt(point, radius = 14) {
    if (!S.ready) return null;
    const towerHit=S.towerLayer?.available?S.towerLayer.hitTest(point,radius):null;
    if(towerHit)return towerHit;
    const layers = ['towers','project-buildings'].filter(id => S.map.getLayer(id));
    const features = S.map.queryRenderedFeatures([[point.x-radius,point.y-radius],[point.x+radius,point.y+radius]], {layers});
    const candidates = new Map();
    features.forEach(f => {
      const g = S.groups.get(f.properties.id); if (!g) return;
      const screen = S.map.project(g.c), distance = Math.hypot(point.x-screen.x,point.y-screen.y);
      const current = candidates.get(g.id);
      // Polygon hits remain selectable at the top of a tall building; anchors rank by proximity.
      const score = f.layer.id === 'towers' ? Math.min(distance, radius + 1) : distance;
      if (!current || score < current.score) candidates.set(g.id, {id:g.id, pid:f.properties.pid, score});
    });
    return [...candidates.values()].sort((a,b) => a.score-b.score || String(a.id).localeCompare(String(b.id)))[0] || null;
  }
  function hoverGroup(id, lngLat, pid) {
    if (!S.ready) return;
    if (!id) {
      S.hovered = null; S.towerLayer?.highlight(null); S.map.setFilter('building-hover',['==',['get','id'],'']); S.map.getCanvas().style.cursor = ''; S.hoverPopup.remove();
      S.map.setFilter('site-hover', ['==',['get','id'],'']);
      document.querySelectorAll('.pmap-site-card.is-hovered').forEach(el=>el.classList.remove('is-hovered')); return;
    }
    const group = S.groups.get(id); if (!group) return;
    const p=pid&&byId(pid),g=p?{...group,members:[p]}:group;
    S.towerLayer?.highlight(id);
    S.map.setFilter('building-hover',['==',['get','id'],id]);
    if (!$('pmap-detail').hidden && g.members.some(p => p.id === S.selected)) { hoverGroup(null); return; }
    S.map.getCanvas().style.cursor = 'pointer';
    if (S.hovered !== (pid||id)) {
      S.hovered = pid||id; S.map.setFilter('site-hover',['==',['get','id'],id]);
      document.querySelectorAll('.pmap-site-card').forEach(el=>el.classList.toggle('is-hovered',g.members.some(p=>p.id===el.dataset.site)));
      const single = g.members.length === 1;
      S.hoverPopup.setHTML(single ? '<span class="popup-kicker">' + esc(g.members[0].d) + ' · ' + (isSale(g.members[0]) ? 'Hardware sales' : 'Project') + '</span>' + popupHtml(g.members[0]) + '<span class="popup-foot">Click to explore this site</span>' :
        '<span class="popup-kicker">At this location</span><b>' + plural(g.members.length,'job') + ' · ' + fmt$(g.total) + '</b>' +
        g.members.slice(0,3).map(p=>'<div>' + esc(p.n) + ' · ' + esc(p.t || p.c) + '</div>').join('') +
        (g.members.length>3 ? '<span class="muted">+' + (g.members.length-3) + ' more</span>' : '') + '<span class="popup-foot">Click to explore these sites</span>');
    }
    S.hoverPopup.setLngLat(lngLat || g.c).addTo(S.map);
  }
  function refreshWater() {
    if(!S.ready||S.map.isMoving()||!S.map.getSource('openmaptiles'))return false;
    const features=S.map.querySourceFeatures('openmaptiles',{sourceLayer:'water'});
    const signature=[S.map.getZoom().toFixed(2),S.map.getCenter().lng.toFixed(4),S.map.getCenter().lat.toFixed(4),features.length].join(':');
    if(signature===S.waterSignature)return false;
    S.waterSignature=signature;
    S.water=features.map(f=>{
      const coords=f.geometry.coordinates.flat(f.geometry.type==='MultiPolygon'?2:1);
      const xs=coords.map(c=>c[0]),ys=coords.map(c=>c[1]);
      return {geometry:f.geometry,bbox:[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]};
    });
    return true;
  }
  function refreshBuildings() {
    if(!S.ready||!S.data||S.map.isMoving()||S.map.getZoom()<14||!S.map.getSource('openmaptiles'))return false;
    const source=S.map.querySourceFeatures('openmaptiles',{sourceLayer:'building'});
    const anchors=[...S.groups.values()].filter(g=>S.map.getBounds().contains(g.c)).map(g=>g.c);
    const signature=[S.map.getZoom().toFixed(2),S.map.getCenter().lng.toFixed(4),S.map.getCenter().lat.toFixed(4),source.length,JSON.stringify(anchors)].join(':');
    if(signature!==S.buildingSignature){S.buildingSignature=signature;S.buildingParts=G.buildingParts(source,anchors);}
    let changed=false;
    for(const g of S.groups.values()){
      if(!S.map.getBounds().contains(g.c))continue;
      const old=S.footprints.get(g.id);
      if(old&&G.distanceToGeometry(g.c,old.geometry)<18)continue;
      const precise=g.members.find(p=>p.q==='exact'||p.src==='manual');if(!precise)continue;
      const found=G.matchBuilding(precise,S.buildingParts||[]);if(!found)continue;
      for(const p of g.members)S.footprints.set(p.id,found);changed=true;
    }
    return changed;
  }
  function stopOrbit() {
    if(!S.orbit)return;
    S.orbit=false;S.map.off('moveend',orbitStep);S.map.stop();
    $('map-orbit').setAttribute('aria-pressed','false');$('map-orbit').textContent='↻ Orbit';
  }
  function orbitStep() {
    if(!S.orbit)return;
    S.map.easeTo({bearing:S.map.getBearing()+120,pitch:55,duration:24000,easing:t=>t});
  }
  function toggleOrbit() {
    if(S.orbit){stopOrbit();return;}if(!S.ready||reducedMotion)return;
    S.orbit=true;hoverGroup(null);$('map-orbit').setAttribute('aria-pressed','true');$('map-orbit').textContent='Ⅱ Orbit';
    S.map.on('moveend',orbitStep);orbitStep();
  }
  function updateThemeButton() {
    const dark = S.styleKey === 'dark'; $('pmap').classList.toggle('night', dark);
    $('map-theme').textContent = dark ? '☀' : '☾';
    $('map-theme').title = dark ? 'Switch to day map' : 'Switch to night map';
    $('map-theme').setAttribute('aria-label', $('map-theme').title);
  }
  function setBasemap(key) {
    if (!S.map) return;
    stopOrbit();
    S.styleKey = ['positron','dark','liberty'].includes(key) ? key : 'positron'; S.ready = false; S.hoverPopup.remove();
    S.map.setStyle(STYLE_URL(S.styleKey), {diff:false}); updateThemeButton(); writeUrl();
  }
  function addLayers() {
    const m = S.map, dark = S.styleKey === 'dark';
    if (m.getLayer('water')) m.setPaintProperty('water','fill-color',dark ? '#183d54' : '#c5dce8');
    if (m.getLayer('background')) m.setPaintProperty('background','background-color',dark ? '#162637' : '#f2f3ef');
    if (m.getLayer('building')) m.setPaintProperty('building','fill-color',dark ? '#294258' : '#cdd7dc');
    if(dark) for(const layer of m.getStyle().layers){
      if(layer.type==='symbol' && layer.layout?.['text-field']){m.setPaintProperty(layer.id,'text-color',/place|settlement|city/.test(layer.id)?'#bed1e2':'#8faabe');m.setPaintProperty(layer.id,'text-halo-color','#172b3e');m.setPaintProperty(layer.id,'text-halo-width',1);}
      if(layer.type==='line' && layer['source-layer']==='transportation'){m.setPaintProperty(layer.id,'line-color',/motorway|trunk/.test(layer.id)?'#405c72':'#2b4256');}
    }
    m.setLight({anchor:'viewport',color:'#fff6e9',intensity:.42,position:[1.5,195,38]});
    if (m.getSource('openmaptiles') && !m.getLayer('bldg3d')) {
      const firstSymbol = (m.getStyle().layers.find(l=>l.type==='symbol') || {}).id;
      m.addLayer({id:'bldg3d',type:'fill-extrusion',source:'openmaptiles','source-layer':'building',minzoom:13,
        filter:['!=',['get','hide_3d'],true], paint:{'fill-extrusion-color':dark?'#344f66':'#d4dde1',
        'fill-extrusion-height':['coalesce',['get','render_height'],8], 'fill-extrusion-base':['coalesce',['get','render_min_height'],0],
        'fill-extrusion-opacity':.88,'fill-extrusion-vertical-gradient':true}}, firstSymbol);
    }
    if (m.getSource('towers')) return;
    for (const id of ['towers','points','project-buildings','route']) m.addSource(id,{type:'geojson',data:fc([])});
    m.addLayer({id:'towers',type:'fill-extrusion',source:'towers',paint:{'fill-extrusion-color':['get','color'],'fill-extrusion-height':['get','h'],'fill-extrusion-base':['get','base'],'fill-extrusion-opacity':1,'fill-extrusion-vertical-gradient':true}});
    m.addLayer({id:'route',type:'line',source:'route',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':dark?'#a7d5f3':'#335d7d','line-width':2,'line-dasharray':[2,2],'line-opacity':.65}});
    m.addLayer({id:'site-hover',type:'circle',source:'points',filter:['==',['get','id'],''],paint:{'circle-radius':7,'circle-color':'#4d9ff1','circle-opacity':.12,'circle-stroke-color':dark?'#d3eaff':'#255f99','circle-stroke-width':1.5}});
    m.addLayer({id:'site-selected',type:'circle',source:'points',filter:['==',['get','sel'],1],paint:{'circle-radius':7,'circle-color':'#ffffff','circle-opacity':.15,'circle-stroke-color':dark?'#e5f3ff':'#183d62','circle-stroke-width':2}});
    m.addLayer({id:'project-buildings',type:'fill-extrusion',source:'project-buildings',minzoom:13,maxzoom:15,paint:{'fill-extrusion-color':['get','color'],'fill-extrusion-height':['get','h'],'fill-extrusion-base':['get','base'],'fill-extrusion-opacity':.92}});
    m.addLayer({id:'building-outlines',type:'line',source:'project-buildings',minzoom:13,paint:{'line-color':['get','color'],'line-width':['case',['==',['get','sel'],1],3,1.5],'line-opacity':.9}});
    m.addLayer({id:'building-hover',type:'line',source:'project-buildings',minzoom:13,filter:['==',['get','id'],''],paint:{'line-color':dark?'#c6e8ff':'#164cb0','line-width':4,'line-opacity':.9}});
    S.towerLayer=null;
    if(window.PCATowerLayer){S.towerLayer=new PCATowerLayer({night:dark,reducedMotion});m.addLayer(S.towerLayer);}
    m.setLayoutProperty('towers','visibility',S.towerLayer?.available?'none':'visible');
    m.addLayer({id:'route-pts',type:'symbol',source:'route',filter:['==',['geometry-type'],'Point'],layout:{'text-field':['get','n'],'text-size':11,'text-font':['Noto Sans Bold'],'text-offset':[0,-1.8],'text-allow-overlap':true},paint:{'text-color':dark?'#d8eafa':'#244868','text-halo-color':dark?'#162c43':'#fff','text-halo-width':1.5}});
  }
  function render(quiet) {
    if (!S.data) return;
    if (!S.ready) { S.visible=S.data.projects.filter(passes).filter(timelineActive); if(!quiet){renderStats();renderLegend({hmax:1});renderSites();renderFilterState();writeUrl();} return; }
    const b = buildFeatures();
    S.map.getSource('points').setData(b.points);
    S.map.getSource('project-buildings').setData(b.buildings);
    if(!S.towerLayer?.available)S.map.getSource('towers').setData(b.towers);
    S.towerLayer?.setData(b.models,{selected:S.placed.get(S.selected)?.group||S.selected,ms:S.tl.on?Math.min(600,+$('tl-speed').value*.8):950});
    $('map-scale-label').textContent = S.map.getPitch()<5?'Overhead':'Project skyline';
    $('map-empty').hidden = S.visible.length > 0;
    if(S.selected&&$('map-location-caption'))$('map-location-caption').textContent=locationCaption(byId(S.selected));
    if (!quiet) {
      hoverGroup(null);
      if (S.selected && !S.visible.some(p=>p.id===S.selected) && !S.data.unlocated.some(p=>p.id===S.selected)) closeDetail(false);
      if(S.openGroup&&S.openGroup.some(id=>!S.visible.some(p=>p.id===id)))closeDetail(false);
      S.listLimit=40; renderStats(); renderLegend(b.ctx); renderSites(); renderFilterState(); writeUrl();
    }
    if (S.f.person) drawRoute();
  }

  function renderStats() {
    const vis=S.visible, projects=vis.filter(p=>!isSale(p)), sales=vis.filter(isSale), ids=new Set(projects.map(p=>p.id));
    const cv=projects.reduce((n,p)=>n+(p.cv||0),0), booked=sales.reduce((n,p)=>n+(p.cv||0),0);
    const hours=projects.reduce((n,p)=>n+(S.tl.on?monthHours(p,S.tl.idx):p.h||0),0);
    const people=S.data.people.filter(per=>per.p.some(x=>ids.has(x[0]))).length;
    const stat=(label,value,title)=>'<div class="st" title="'+esc(title)+'"><div class="label">'+label+'</div><div class="val">'+value+'</div></div>';
    $('pmap-stats').innerHTML=stat('Sites on map',vis.length.toLocaleString(),plural(projects.length,'project')+(sales.length?' · '+plural(sales.length,'010 customer site'):''))
      +stat('Project contracts',fmt$(cv),'Contract value of matching projects'+(sales.length?' · 010 bookings separately: '+fmt$(booked):''))
      +stat(S.tl.on?'Hours this month':'Hours in window',Math.round(hours).toLocaleString(),'PTT hours on matching projects')
      +stat('People in window',people.toLocaleString(),'People with PTT hours on matching projects during the full time window');
  }
  function renderLegend(ctx) {
    const L=$('pmap-legend');
    if(!L.querySelector('.pmap-divs')) L.innerHTML='<div class="pmap-divs"></div><div class="pmap-legend-items"></div>';
    const sig=S.divs.map(d=>d.k).join('|');
    if(L.dataset.sig!==sig){
      L.dataset.sig=sig;
      L.querySelector('.pmap-divs').innerHTML=S.divs.map(d=>'<button type="button" class="chip" data-div="'+esc(d.k)+'" title="'+esc(d.k+' · '+(d.name||'Division')+' · Shift-click to show only this division')+'" aria-label="'+esc(d.k+' '+(d.name||'Division'))+'"><i style="background:'+divColor(d.k)+'"></i><b>'+esc(d.k)+'</b></button>').join('');
    }
    L.querySelectorAll('[data-div]').forEach(el=>{const on=!S.f.hideDiv.has(el.dataset.div);el.classList.toggle('on',on);el.setAttribute('aria-pressed',String(on));});
    const item=(c,t)=>'<span><i style="background:'+c+'"></i>'+esc(t)+'</span>';
    let items='';
    if(S.color==='s') items=STATE_ORDER.filter(k=>S.visible.some(p=>p.s===k)).map(k=>item(STATE_COLOR[k],STATE_LABEL[k])).join('');
    else if(S.color==='sol') items=[...(S.catColors.sol||new Map()).entries()].map(([k,c])=>item(c,k.replace(/_/g,' '))).join('');
    else if(S.color==='q') items=Object.entries(Q_LABEL).map(([k,v])=>item(Q_COLOR[k],v)).join('');
    else if(S.color==='heat') items='<span>Less <span class="grad" style="background:linear-gradient(90deg,#ffd166,#f4511e,#8b0000)"></span>More activity</span>';
    else if(S.color==='gpp') items='<span>−20% <span class="grad" style="background:linear-gradient(90deg,#c62828,#e9edf2,#178a52)"></span>+40% GP</span>';
    L.querySelector('.pmap-legend-items').innerHTML=items;
  }
  function siteCard(p) {
    const active=S.tl.on?monthHours(p,S.tl.idx):p.h||0;
    return '<button type="button" class="pmap-site-card'+(S.selected===p.id?' is-selected':'')+'" data-site="'+esc(p.id)+'" aria-label="Explore '+esc(p.n+' '+p.t)+'">'
      +'<span class="pmap-site-top"><i style="background:'+divColor(p.d)+'"></i><span>'+esc(p.d)+' · '+esc(isSale(p)?'Hardware sales':p.n)+'</span><span>'+(p.q==='city'?'≈ City location':isSale(p)?plural(p.orders,'order'):esc(STATE_LABEL[p.s]||p.s))+'</span></span>'
      +'<strong>'+esc(p.t||p.c)+'</strong>'+(p.c&&p.c!==p.t?'<span class="customer">'+esc(p.c)+'</span>':'')
      +'<span class="pmap-site-bottom"><b>'+fmt$(p.cv)+'</b><span style="color:#7990a5;font-size:9px">'+(isSale(p)?'booked':'contract')+'</span>'+(active>0?'<span class="activity">'+fmtH(active)+'</span>':'')+'</span>'
      +(!isSale(p)&&p.pct!=null?'<div class="pmap-progress" title="'+pct0(p.pct)+' complete"><span style="width:'+Math.max(0,Math.min(100,p.pct*100))+'%"></span></div>':'')+'</button>';
  }
  function renderSites() {
    if(!S.data)return;
    let list=S.visible;
    if(S.inView&&S.ready)list=list.filter(p=>S.map.getBounds().contains([p.lng,p.lat]));
    list=list.slice().sort(S.listSort==='name'?(a,b)=>(a.t||a.c).localeCompare(b.t||b.c):S.listSort==='h'?(a,b)=>(b.h||0)-(a.h||0):(a,b)=>(b.cv||0)-(a.cv||0));
    $('sites-n').textContent=list.length.toLocaleString();
    $('pmap-sites').innerHTML=list.length?list.slice(0,S.listLimit).map(siteCard).join(''):'<div class="empty">'+(S.inView?'No matching sites in this view. Pan the map or turn off “In view”.':'No matching sites. Try another search or reset the filters.')+'</div>';
    $('sites-more').hidden=list.length<=S.listLimit;
    if(!$('sites-more').hidden)$('sites-more').textContent='Show more · '+(list.length-S.listLimit)+' remaining';
  }
  function renderFilterState() {
    const f=S.f, chips=[];
    const add=(key,label)=>chips.push('<button class="pmap-filter-pill" data-clear-filter="'+key+'" aria-label="Clear '+esc(label)+'">'+esc(label)+'<span>×</span></button>');
    if(f.states.size<STATE_ORDER.length)add('states',f.states.size===1?STATE_LABEL[[...f.states][0]]:f.states.size+' statuses');
    if(f.hideDiv.size)add('hideDiv',(S.divs.length-f.hideDiv.size)+' divisions');
    for(const [key,label] of [['pm','PM'],['mode','Type'],['sol','Solution'],['sec','Sector']])if(f[key])add(key,key==='pm'?($('f-pm').selectedOptions[0]?.textContent||label):label+': '+f[key].replace(/_/g,' '));
    if(f.mincv)add('mincv',fmt$(f.mincv)+'+');
    if(f.crew)add('crew','With crew');
    if(f.person)add('person',S.data.people.find(p=>p.k===f.person)?.n||'Selected person');
    $('pmap-active-filters').innerHTML=chips.join('');
    $('filter-n').hidden=!chips.length; $('filter-n').textContent=chips.length;
    $('f-states').querySelectorAll('[data-state]').forEach(el=>{el.classList.toggle('on',f.states.has(el.dataset.state));el.setAttribute('aria-pressed',String(f.states.has(el.dataset.state)));});
  }
  function setTab(tab) {
    S.tab=tab;
    document.querySelectorAll('[data-tab]').forEach(el=>{const active=el.dataset.tab===tab;el.setAttribute('aria-selected',String(active));el.tabIndex=active?0:-1;$('panel-'+el.dataset.tab).hidden=!active;});
  }
  function setExplorer(minimized, remember=true) {
    $('pmap').classList.toggle('explorer-minimized',minimized); $('explorer-toggle').textContent=minimized?'+':'−';
    $('explorer-toggle').setAttribute('aria-expanded',String(!minimized));
    $('explorer-toggle').setAttribute('aria-label',minimized?'Expand explorer':'Minimize explorer');
    $('explorer-toggle').title=minimized?'Expand explorer':'Minimize explorer';
    if(remember&&typeof PCA!=='undefined')PCA.setPref('map-explorer-minimized',minimized);
  }
  function byId(id) { return S.byId?.get(id); }

  function popupHtml(p) {
    const approx = p.q !== 'exact' && p.src !== 'manual' ? '<br><span class="muted">≈ ' + esc(Q_LABEL[p.q]) + '</span>' : '';
    if (isSale(p)) {
      return '<b>' + esc(p.c) + (p.t ? ' · ' + esc(p.t) : '') + '</b><span class="muted">010 hardware sales' + (p.sec ? ' · ' + esc(p.sec) : '') + '</span><br>'
        + '<b style="display:inline">' + fmt$(p.cv) + '</b> booked · ' + plural(p.orders, 'order') + (p.open_n ? ' · ' + p.open_n + ' open' : '') + (p.bill ? '<br>' + fmt$(p.bill) + ' invoiced' : '') + approx;
    }
    const gp = S.data.margins && !isClosed(p) && p.egpp != null ? ' · forecast GP ' + pct1(p.egpp) : (S.data.margins && p.gpp != null ? ' · GP ' + pct1(p.gpp) : '');
    return '<b>' + esc(p.n) + ' · ' + esc(p.t) + '</b><span class="muted">' + esc(p.c || '—') + ' · ' + esc(STATE_LABEL[p.s] || p.s) + ' · div ' + esc(p.d) + '</span><br>'
      + 'Contract ' + fmt$(p.cv) + (p.pct != null ? ' · ' + pct0(p.pct) + ' complete' : '') + gp + (p.h ? '<br><b style="display:inline">' + fmtH(p.h) + '</b> by ' + p.hc + ' in window' : '') + approx;
  }

  // ------------------------------------------------------------------ selection + detail panel
  const personLink = (name, key) => key && CFG.people ? '<a href="/people/' + esc(key) + '/" title="Open the person page">' + esc(name) + '</a>' : esc(name || '—');
  const custLink = p => p.cid && CFG.customers ? '<a href="/customers/' + esc(p.cid) + '/" title="Open the customer page">' + esc(p.c) + '</a>' : esc(p.c || '—');

  function closeDetail(redraw = true) {
    $('pmap-detail').hidden = true; $('pmap').classList.remove('detail-open');
    S.selected = null; S.openGroup = null;
    if (redraw && S.ready) { render(true); renderSites(); }
  }
  function detailShell(html) {
    const D=$('pmap-detail'); D.hidden=false; $('pmap').classList.add('detail-open');
    D.innerHTML='<button class="close" data-close aria-label="Close site details">✕</button>' + html;
    D.scrollTop=0; if(S.ready)hoverGroup(null);
  }
  function locationCaption(p) {
    if(!p)return '';
    return (p.q==='city'?'≈ City location':p.q==='approx'?'≈ Approximate street match':SRC_LABEL[p.src]||'Location unavailable')+(S.placed.get(p.id)?.building?' · Mapped building footprint':'');
  }
  function select(id, fly) {
    const p=byId(id); if(!p) return; S.selected=id; S.openGroup=null;
    detailShell((isSale(p) ? detailSale(p) : detailProject(p)));
    const title = $('pmap-detail').querySelector('h2');
    const actions=document.createElement('div'); actions.className='pmap-detail-action';
    actions.innerHTML=(p.lat != null ? '<button data-zoom-site="' + esc(id) + '">⌖ Zoom to site</button>' : '') + (!isSale(p) ? '<a href="/projects/' + esc(p.id) + '/">Open project ↗</a>' : '');
    title.after(actions);
    const provenance=document.createElement('div');provenance.className='pmap-caption';provenance.id='map-location-caption';
    provenance.textContent=locationCaption(p);
    actions.after(provenance);
    const group=S.groups.get(S.placed.get(id)?.group);
    if(group?.members.length>1){const shared=document.createElement('button');shared.className='pmap-shared-address';shared.textContent=plural(group.members.length-1,'other job')+' at this address ↗';shared.onclick=()=>openGroup(group);provenance.after(shared);}
    if(fly && p.lat != null) flyToSite(p);
    render(true); renderSites();
  }
  function flyToSite(p) {
    if(!S.map||!p)return;stopOrbit();
    const width=$('map').clientWidth, detail=!$('pmap-detail').hidden;
    const right=detail ? Math.min(440,width*.48) : 60;
    const left=detail && width<1100 || $('pmap').classList.contains('explorer-minimized') ? 30 : Math.min(320,width*.35);
    S.map.flyTo({center:[p.lng,p.lat],zoom:16,pitch:58,bearing:-18,offset:[(left-right)/2,-20],duration:duration(1100)});
  }
  function openGroup(g) {
    S.selected=null; S.openGroup=g.members.map(p=>p.id);
    const spread=g.members.some(p=>G.meters(g.c,[p.lng,p.lat])>15);
    detailShell('<span class="pmap-eyebrow">' + (spread?'IN THIS AREA':'AT THIS LOCATION') + '</span><h2>' + plural(g.members.length,'job') + ' · ' + fmt$(g.total) + '</h2><p class="pmap-group-label">' + esc(spread ? g.members[0].addr || 'Projects and customer sites nearby' : g.members[0].addr || 'Shared address') + '</p>' +
      (spread ? '<div class="pmap-detail-action"><button data-zoom-group>Zoom into this area ↗</button></div>' : '') + g.members.map(siteCard).join(''));
    render(true);
  }

  function healthLine(p) {
    // the one-glance verdict: forecast (or final) margin vs sold, % complete, hours vs budget, over/under-billing, risk
    const closed = isClosed(p), m = S.data.margins, bits = [], lv = [];
    if (m && !closed && p.egpp != null) {
      bits.push('Forecast GP <b>' + pct1(p.egpp) + '</b>' + (p.dpts != null ? ' <span class="' + sgn(p.dpts) + '">' + pts(p.dpts) + ' vs sold</span>' : ''));
      lv.push(p.egpp < 0 || (p.dpts != null && p.dpts <= -0.05) ? 'bad' : (p.dpts != null && p.dpts <= -0.02) ? 'warn' : 'good');
    } else if (m && p.gpp != null) {
      const dd = p.sgpp != null ? p.gpp - p.sgpp : null;
      bits.push((closed ? 'Final GP <b>' : 'GP to date <b>') + pct1(p.gpp) + '</b>' + (dd != null ? ' <span class="' + sgn(dd) + '">' + pts(dd) + ' vs sold</span>' : ''));
      lv.push(p.gpp < 0 || (dd != null && dd <= -0.05) ? 'bad' : (dd != null && dd <= -0.02) ? 'warn' : 'good');
    }
    if (p.pct != null) bits.push('<b>' + pct0(p.pct) + '</b> complete' + (p.pctat ? ' <span class="muted">(PM, ' + esc(p.pctat) + ')</span>' : ' <span class="muted">(PM)</span>'));
    if (p.bh > 0 && p.hrs != null) {
      const u = p.hrs / p.bh;
      bits.push('<b>' + Math.round(u * 100) + '%</b> of budget hours used' + (!closed && p.hor != null ? ' → <span class="' + (p.hor > 1.1 ? 'neg' : '') + '">' + Math.round(p.hor * 100) + '% at completion</span>' : ''));
      if (!closed && ((p.hor != null && p.hor > 1.15) || (u > 1 && (p.pct == null || p.pct < 1)))) lv.push('warn');
    }
    if (p.earn != null && p.bill != null && p.cv > 0) { const ob = p.bill - p.earn; if (Math.abs(ob) >= 1000) bits.push((ob > 0 ? 'overbilled ' : 'underbilled ') + '<b>' + fmt$(Math.abs(ob)) + '</b>'); }
    if (m && p.risk) bits.push('risk <span class="tag ' + esc(p.risk) + '" title="' + esc((p.rwhy || []).join(' · ')) + '">' + esc(p.risk) + (p.rscore != null ? ' ' + p.rscore : '') + '</span>');
    const level = lv.includes('bad') ? 'bad' : lv.includes('warn') ? 'warn' : lv.includes('good') ? 'good' : '';
    return bits.length ? '<div class="pmap-health ' + level + '">' + bits.join(' · ') + '</div>' : '';
  }
  function scoreTable(p) {
    const closed = isClosed(p), m = S.data.margins, fc3 = !closed && p.asof != null;
    const cell = (v, cls) => '<td class="num ' + (cls || '') + '">' + fmt$(v) + '</td>';
    let h = '<table class="pmap-score"><thead><tr><th></th><th title="Current SL budget (SL keeps no original budget).">Sold</th><th title="Posted in SL so far.">To date</th>' + (fc3 ? '<th title="Deterministic EAC: posted labor + unposted PTT hours × rate + PM remaining hours × rate; non-labor at least budget, or actual + open commitments if higher.">At completion</th>' : '') + '</tr></thead><tbody>';
    h += '<tr><td>Revenue</td>' + cell(p.cv) + cell(p.bill) + (fc3 ? cell(p.erev) : '') + '</tr>';
    if (m) {
      h += '<tr><td>Direct cost</td>' + cell(p.bud) + cell(p.cost) + (fc3 ? cell(p.ecost, p.bud != null && p.ecost > p.bud * 1.02 ? 'neg' : '') : '') + '</tr>';
      h += '<tr class="strong"><td>Gross profit</td>' + cell(p.sgp) + cell(p.gp, sgn(p.gp)) + (fc3 ? cell(p.egp, sgn(p.egp)) : '') + '</tr>';
      h += '<tr class="muted"><td>GP margin</td><td class="num">' + pct1(p.sgpp) + '</td><td class="num">' + pct1(p.gpp) + '</td>' + (fc3 ? '<td class="num">' + pct1(p.egpp) + (p.dpts != null ? ' <span class="' + sgn(p.dpts) + '">' + pts(p.dpts) + '</span>' : '') + '</td>' : '') + '</tr>';
    } else {
      h += '<tr><td>Direct cost</td><td class="num">—</td>' + cell(p.cost) + (fc3 ? '<td class="num">—</td>' : '') + '</tr>';
    }
    h += '<tr><td>Labor hours</td><td class="num">' + (p.bh ? fmtH(p.bh) : '—') + '</td><td class="num">' + fmtH(p.hrs) + '</td>' + (fc3 ? '<td class="num">' + (p.ehrs != null ? fmtH(p.ehrs) : '—') + (p.hor != null ? ' <span class="' + (p.hor > 1.1 ? 'neg' : 'muted') + '">' + Math.round(p.hor * 100) + '%</span>' : '') + '</td>' : '') + '</tr>';
    return h + '</tbody></table>';
  }
  function progressBars(p) {
    const closed = isClosed(p);
    const bar = (label, used, total, marker, caption, warn) => {
      const w = total > 0 ? Math.min(100, used / total * 100) : 0, mk = marker != null && total > 0 ? Math.min(100, marker / total * 100) : null;
      return '<div class="pmap-bar' + (warn ? ' warn' : '') + '"><div class="lbl"><span>' + label + '</span><span>' + caption + '</span></div><div class="bar"><span style="width:' + w + '%"></span>' + (mk != null ? '<i class="mark" style="left:' + mk + '%"></i>' : '') + '</div></div>';
    };
    let h = '';
    if (p.cv > 0) {
      const ob = p.earn != null && p.bill != null ? p.bill - p.earn : null;
      h += bar('Billing', p.bill || 0, p.cv, p.earn, fmt$(p.bill) + ' of ' + fmt$(p.cv) + (ob != null && Math.abs(ob) >= 1000 ? ' · ' + (ob > 0 ? 'overbilled ' : 'underbilled ') + fmt$(Math.abs(ob)) : '') + (p.earn != null ? ' · ▏earned ' + fmt$(p.earn) : ''), false);
    }
    if (p.cost > 0 || p.bud > 0) {
      const total = Math.max(p.cost || 0, p.ecost || 0, p.bud || 0);
      h += bar('Cost', p.cost || 0, total, p.bud, fmt$(p.cost) + ' spent' + (!closed && p.ecost != null ? ' of ' + fmt$(p.ecost) + ' projected' : '') + (p.bud != null ? ' · ▏budget ' + fmt$(p.bud) : ''), !closed && p.bud != null && p.ecost > p.bud * 1.02);
    }
    if (p.hrs > 0 || p.bh > 0) {
      const total = Math.max(p.hrs || 0, p.ehrs || 0, p.bh || 0);
      h += bar('Hours', p.hrs || 0, total, p.bh, fmtH(p.hrs) + ' worked' + (!closed && p.rem != null ? ' · ' + fmtH(p.rem) + ' remaining (PM)' : '') + (p.bh ? ' · ▏budget ' + fmtH(p.bh) : ''), !closed && p.bh > 0 && Math.max(p.ehrs || 0, p.hrs || 0) > p.bh * 1.02);
    }
    return h ? '<div class="pmap-bars">' + h + '</div>' : '';
  }
  function detailProject(p) {
    const gated = S.data.margins, closed = isClosed(p);
    const crew = (p.crew || []).map(c => '<div class="row"><span>' + personLink(c[0], c[2]) + ' <span class="muted">' + plural(c[3], 'day') + (c[4] ? ' · ' + esc(c[4].slice(5)) + (c[5] && c[5] !== c[4] ? '→' + esc(c[5].slice(5)) : '') : '') + '</span></span><span class="num">' + fmtH(c[1]) + '</span></div>').join('')
      + (p.crew_more ? '<div class="note">+ ' + p.crew_more + ' more</div>' : '');
    return '<h2><a href="/projects/' + esc(p.id) + '/" title="Open the project page">' + esc(p.n) + '</a> ' + esc(p.t) + '</h2>'
      + '<div class="sub">' + custLink(p) + (p.sec ? ' · ' + esc(p.sec) : '') + '</div>'
      + '<div class="row" style="gap:6px;margin-bottom:8px;flex-wrap:wrap"><span class="tag ' + esc(p.s) + '">' + esc(STATE_LABEL[p.s] || p.s) + '</span><span class="pmap-tag">' + esc(MODE_LABEL[p.m] || p.m) + '</span>' + (p.sol ? '<span class="pmap-tag">' + esc(p.sol.replace(/_/g, ' ')) + '</span>' : '') + '<span class="pmap-tag" style="background:' + divColor(p.d) + '22;color:' + divColor(p.d) + '">div ' + esc(p.d) + '</span></div>'
      + healthLine(p)
      + '<h4>Scoreboard <span class="muted" style="text-transform:none;letter-spacing:0">' + (closed ? 'sold → final' : 'sold → to date → forecast' + (p.asof ? ' · EAC as of ' + esc(p.asof) : ' · no EAC yet')) + '</span></h4>'
      + scoreTable(p) + progressBars(p)
      + (gated && p.rwhy && p.rwhy.length && p.risk && p.risk !== 'low' ? '<div class="note">' + esc(p.rwhy.join(' · ')) + '</div>' : '')
      + '<dl class="kv dense" style="margin-top:10px"><dt>Project manager</dt><dd>' + personLink(p.pm, p.pmk) + '</dd><dt>Created</dt><dd>' + esc(p.cr || '—') + '</dd><dt>Work</dt><dd>' + esc(p.fw || '—') + ' → ' + esc(p.lw || '—') + (p.cl ? ' · closed ' + esc(p.cl) : '') + '</dd>'
      + '<dt>Hours</dt><dd>' + fmtH(p.hrs) + ' lifetime · ' + plural(p.wk || 0, 'person').replace('persons', 'people') + (p.h30 ? ' · ' + fmtH(p.h30) + ' last 30 days' : '') + (p.unp ? ' · ' + fmtH(p.unp) + ' not yet in SL' : '') + '</dd>'
      + (p.lpct != null ? '<dt title="Hours worked ÷ (worked + PM remaining).">Labor % (calc)</dt><dd>' + pct0(p.lpct) + '</dd>' : '') + '</dl>'
      + '<h4>Crew in window <span class="muted">' + (p.h ? fmtH(p.h) + ' · ' + plural(p.hc, 'person').replace('persons', 'people') : 'no hours') + '</span></h4><div class="crew">' + (crew || '<div class="note">Nobody logged PTT hours here in this window.</div>') + '</div>'
      + locationBlock(p, true);
  }
  function detailSale(p) {
    const m = S.data.margins;
    const reps = (p.reps || []).map(r => '<div class="row"><span>' + esc(r[0]) + ' <span class="muted">' + plural(r[2], 'order') + '</span></span><span class="num">' + fmt$(r[1]) + '</span></div>').join('') + (p.reps_more ? '<div class="note">+ ' + p.reps_more + ' more</div>' : '');
    const orders = (p.top || []).map(o => '<div class="row"><span><a href="/sales/010/orders/' + esc(o[0]) + '/" title="Open the order">' + esc(o[0]) + '</a> <span class="muted">' + esc(o[1] || '') + (o[4] ? ' · ' + esc(o[4]) : '') + '</span>' + (o[3] === 'open' ? ' <span class="pmap-tag open">open</span>' : '') + '</span><span class="num">' + fmt$(o[2]) + '</span></div>').join('')
      + (p.orders > (p.top || []).length ? '<div class="note">+ ' + (p.orders - p.top.length) + ' more · <a href="/sales/010/orders/?q=' + encodeURIComponent(p.c) + '">all orders for ' + esc(p.c) + '</a></div>' : '');
    const kv = [['Booked', fmt$(p.cv), plural(p.orders, 'order') + ' in the window (CNET totals, cancelled excluded)'],
      ['Invoiced', fmt$(p.bill), 'SL shippers on those orders'],
      ['Open backlog', p.open_n ? fmt$(p.open_amt) : '—', p.open_n ? plural(p.open_n, 'order') + ' still open in SL' : 'nothing open']];
    if (m) { kv.push(['Shipped cost', fmt$(p.cost), 'SL shipper cost']); kv.push(['Realized GP', p.gp != null ? fmt$(p.gp) + (p.gpp != null ? ' · ' + pct1(p.gpp) : '') : '—', 'invoiced − shipped cost']); if (p.qgpp != null) kv.push(['Quoted GM', pct1(p.qgpp), 'CNET price vs CNET cost']); }
    return '<h2>' + custLink(p) + '</h2>'
      + '<div class="sub">010 Hardware Sales' + (p.t ? ' · ' + esc(p.t) : '') + (p.sec ? ' · ' + esc(p.sec) : '') + '</div>'
      + '<div class="row" style="gap:6px;margin-bottom:8px"><span class="pmap-tag sale">010 sales site</span><span class="pmap-tag">' + plural(p.orders, 'order') + '</span>' + (p.fw ? '<span class="pmap-tag">' + esc(p.fw) + (p.lw && p.lw !== p.fw ? ' → ' + esc(p.lw) : '') + '</span>' : '') + '</div>'
      + '<div class="pmap-health ' + (m && p.gpp != null ? (p.gpp < 0.05 ? 'bad' : p.gpp < 0.12 ? 'warn' : 'good') : '') + '"><b>' + fmt$(p.cv) + '</b> booked · ' + plural(p.orders, 'order') + (p.open_n ? ' · <b>' + p.open_n + '</b> open (' + fmt$(p.open_amt) + ' backlog)' : '') + (m && p.gpp != null ? ' · realized GP <b>' + pct1(p.gpp) + '</b>' : '') + '</div>'
      + '<table class="pmap-score"><tbody>' + kv.map(r => '<tr><td>' + r[0] + '<div class="muted" style="font-size:11px">' + r[2] + '</div></td><td class="num">' + r[1] + '</td></tr>').join('') + '</tbody></table>'
      + '<h4>Salespeople</h4><div class="crew">' + (reps || '<div class="note">—</div>') + '</div>'
      + '<h4>Orders in window <span class="muted">largest first</span></h4><div class="crew orders">' + (orders || '<div class="note">—</div>') + '</div>'
      + locationBlock(p, false);
  }
  function locationBlock(p, fixable) {
    const q = p.src === 'manual' ? 'manual' : p.q;
    return '<h4>Location</h4>' + '<div class="loc">' + (p.lat != null ? '<span class="pmap-tag ' + esc(q) + '">' + esc(p.src === 'manual' ? 'pinned by hand' : Q_LABEL[p.q]) + '</span> <span class="muted">via ' + esc(SRC_LABEL[p.src] || p.src) + '</span><div>' + esc(p.addr || '') + (p.site && !isSale(p) ? ' <span class="muted">(' + esc(p.site) + ')</span>' : '') + '</div><div class="muted mono">' + p.lat.toFixed(5) + ', ' + p.lng.toFixed(5) + '</div>' : '<span class="pmap-tag city">not located</span> no shipping or customer address found')
      + (fixable ? '<details' + (p.lat == null ? ' open' : '') + '><summary style="cursor:pointer;margin-top:6px">fix location</summary><input type="text" id="loc-where" placeholder="street address, or  41.8781, -87.6298"><input type="text" id="loc-note" placeholder="note (optional)"><div class="row"><button class="btn small primary" id="loc-save">Pin here</button>' + (p.src === 'manual' ? '<button class="btn small" id="loc-reset">Back to automatic</button>' : '') + '<span class="muted" id="loc-msg"></span></div><div class="note">Stored locally only — SL is never written.</div></details>'
        : '<div class="note" style="margin-top:6px">Where the orders shipped per CNET; grouped by customer and address. Fix the address in CNET if it is wrong.</div>') + '</div>';
  }
  function locate(action) {
    const p = byId(S.selected) || S.data.unlocated.find(x => x.id === S.selected); if (!p || isSale(p)) return;
    const body = new URLSearchParams({cpn: p.id, action: action || '', where: ($('loc-where') || {}).value || '', note: ($('loc-note') || {}).value || ''});
    $('loc-msg').textContent = 'working…';
    fetch(CFG.locate_url, {method: 'POST', headers: {'X-CSRFToken': csrf(), 'Content-Type': 'application/x-www-form-urlencoded'}, body}).then(r => r.json()).then(res => {
      if (!res.ok) { $('loc-msg').textContent = res.error || 'failed'; return; }
      $('loc-msg').textContent = 'saved — reloading';
      const keep = S.selected; load().then(() => { if (byId(keep)) select(keep, true); });
    }).catch(() => { $('loc-msg').textContent = 'failed'; });
  }
  function csrf() { const m = document.cookie.match(/csrftoken=([^;]+)/); return m ? m[1] : ''; }

  // ------------------------------------------------------------------ people / route
  function selectPerson(key, keepUrl) {
    S.f.person = S.f.person === key && !keepUrl ? '' : key;
    renderPeople(); render();
    if (!S.f.person) { if (S.ready) S.map.getSource('route').setData(fc([])); if(!S.tl.on)$('pmap-tl-banner').hidden=true; return; }
    drawRoute();
    const per = S.data.people.find(x => x.k === S.f.person);
    if (per) {
      const pts_ = per.p.map(x => S.placed.get(x[0])).filter(Boolean).map(x => x.anchor);
      const local = pts_.filter(c => c[1] > 41.2 && c[1] < 42.6 && c[0] > -88.7 && c[0] < -87.2);
      // stay on Chicagoland when most of the person's sites are here; the out-of-area buttons reach the rest
      if (pts_.length) fitTo(local.length >= pts_.length * 0.6 ? local : pts_);
    }
  }
  function drawRoute() {
    const per = S.data.people.find(x => x.k === S.f.person); if (!per || !S.ready || !S.map.getSource('route')) return;
    const stops = per.p.slice().sort((a, b) => (a[2] || '').localeCompare(b[2] || '')).map((x, i) => ({x, pl: S.placed.get(x[0]), i})).filter(s => s.pl);
    const feats = stops.map((s, i) => ({type: 'Feature', properties: {n: String(i + 1), id: s.x[0]}, geometry: {type: 'Point', coordinates: s.pl.anchor}}));
    if (stops.length > 1) feats.push({type: 'Feature', properties: {}, geometry: {type: 'LineString', coordinates: stops.map(s => s.pl.anchor)}});
    S.map.getSource('route').setData(fc(feats));
    const banner = $('pmap-tl-banner');
    if (!S.tl.on) { banner.hidden = false; banner.innerHTML = '<b>' + personLink(per.n, per.k).replace('<a ', '<a style="color:#9fc1ff" ') + '</b> · ' + fmtH(per.h) + ' across ' + plural(per.p.length, 'project') + ' in the window · site sequence · <a href="#" data-person-clear style="color:#9fc1ff">clear</a>'; }
  }

  // ------------------------------------------------------------------ views
  function fitTo(pts_) {
    if (!pts_.length || !S.map) return;
    const b = pts_.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(pts_[0], pts_[0]));
    const width=$('map').clientWidth, detail=!$('pmap-detail').hidden;
    S.map.fitBounds(b, {padding:{top:100,bottom:160,left:detail&&width<1100||$('pmap').classList.contains('explorer-minimized')?40:Math.min(330,width*.38),right:detail?Math.min(445,width*.5):60},pitch:45,duration:duration(1100),maxZoom:16});
  }

  // ------------------------------------------------------------------ timeline
  function setPlayState(playing) {
    $('tl-play').textContent=playing?'❚❚':'▶'; $('tl-play').setAttribute('aria-pressed',String(playing));
    $('tl-play').setAttribute('aria-label',playing?'Pause timeline':'Play timeline');
  }
  function tlStart() {
    if(!S.data || S.data.window.months.length<2) return;
    closeDetail(); clearTimeout(S.tl.timer); S.tl.on=true; S.tl.paused=false; setPlayState(true); $('tl-stop').hidden=false;
    if(S.tl.idx>=S.data.window.months.length-1) S.tl.idx=0;
    tlTick();
  }
  function tlTick() {
    tlShow();
    S.tl.timer=setTimeout(()=>{if(!S.tl.on)return;if(S.tl.idx<S.data.window.months.length-1){S.tl.idx++;tlTick();}else tlPause();},+$('tl-speed').value);
  }
  function tlShow() {
    $('tl-scrub').value=S.tl.idx; $('tl-label').textContent=monthLabel(S.data.window.months[S.tl.idx]);
    $('tl-scrub').setAttribute('aria-valuetext',$('tl-label').textContent); $('timeline-mode').textContent='Monthly replay';
    if(S.selected&&!timelineActive(byId(S.selected)))closeDetail(false);
    render(true); renderSites(); renderStats();
    const projs=S.visible.filter(p=>!isSale(p)), sales=S.visible.filter(isSale);
    const mh=projs.reduce((n,p)=>n+monthVal(p,S.tl.idx),0), act=projs.filter(p=>monthVal(p,S.tl.idx)>0).length;
    const booked=sales.reduce((n,p)=>n+monthVal(p,S.tl.idx),0);
    const banner=$('pmap-tl-banner'); banner.hidden=false;
    banner.innerHTML='<b>'+esc(monthLabel(S.data.window.months[S.tl.idx]))+'</b> · '+plural(act,'active project')+' · '+fmtH(mh)+(sales.length?' · '+fmt$(booked)+' booked':'')+'<br><span style="opacity:.7">Size: accumulated hours'+(sales.length?' / hardware bookings':'')+'</span>';
  }
  function tlPause() {clearTimeout(S.tl.timer); S.tl.timer=null; S.tl.paused=true; setPlayState(false);}
  function tlStop() {
    clearTimeout(S.tl.timer); S.tl.timer=null; S.tl.on=false; S.tl.paused=false; setPlayState(false);
    $('tl-stop').hidden=true; $('pmap-tl-banner').hidden=true; $('timeline-mode').textContent='Activity window';
    if(!S.data)return;
    S.tl.idx=S.data.window.months.length-1; $('tl-scrub').value=S.tl.idx; $('tl-label').textContent=windowLabel();
    $('tl-scrub').setAttribute('aria-valuetext','Full time window'); render();
  }

  // ------------------------------------------------------------------ wiring
  function toggleDiv(code, only) {
    const all = S.divs.map(x => x.k);
    if (only) {
      const soloed = S.f.hideDiv.size === all.length - 1 && !S.f.hideDiv.has(code);
      S.f.hideDiv = soloed ? new Set() : new Set(all.filter(k => k !== code));
    } else if (S.f.hideDiv.has(code)) S.f.hideDiv.delete(code); else S.f.hideDiv.add(code);
    render();
  }
  function resetFilters() {
    S.f=freshFilters();if(!S.data)return;buildOptions();
    if(S.ready)S.map.getSource('route').setData(fc([]));
    $('pmap-tl-banner').hidden=true;render();
  }
  function clearFilter(key) {
    if(key==='states')S.f.states=new Set(STATE_ORDER);
    else if(key==='hideDiv')S.f.hideDiv.clear();
    else if(key==='person'){selectPerson('');return;}
    else S.f[key]=key==='crew'?false:key==='mincv'?0:'';
    buildOptions();render();
  }
  function setMobileNav(open) {
    document.body.classList.toggle('map-nav-open',open);$('map-nav-scrim').hidden=!open;
    document.querySelector('.sidebar').inert=window.innerWidth<=700&&!open;
    $('map-navigation').setAttribute('aria-expanded',String(open));
    $('map-navigation').setAttribute('aria-label',open?'Close navigation':'Open navigation');
    if(open)document.querySelector('.sidebar a.active')?.focus();else $('map-navigation').focus();
  }
  function wire() {
    const on=(id,event,fn)=>$(id).addEventListener(event,fn);
    on('f-w','change',e=>{const v=e.target.value;if(v==='custom'){$('f-custom').hidden=false;return;}$('f-custom').hidden=true;CFG.window=v;load();});
    on('f-apply','click',()=>{const a=$('f-from').value,b=$('f-to').value;if(a&&b){CFG.window=a+'..'+b;load();}});
    ['f-mode','f-sol','f-sec','f-pm'].forEach(id=>on(id,'change',e=>{S.f[id.slice(2)]=e.target.value;render();}));
    on('f-mincv','change',e=>{S.f.mincv=+e.target.value;render();});
    let searchTimer;
    on('f-q','input',e=>{clearTimeout(searchTimer);S.f.q=e.target.value.trim();searchTimer=setTimeout(()=>render(),160);});
    on('f-q','keydown',e=>{if(e.key==='Enter'&&S.data){clearTimeout(searchTimer);render();if(S.visible.length===1)select(S.visible[0].id,true);else fitTo(S.visible.map(p=>[p.lng,p.lat]));}});
    on('f-crew','change',e=>{S.f.crew=e.target.checked;render();});
    on('f-states','click',e=>{const el=e.target.closest('[data-state]');if(!el)return;const key=el.dataset.state;if(S.f.states.has(key))S.f.states.delete(key);else S.f.states.add(key);render();});
    on('pmap-legend','click',e=>{const el=e.target.closest('[data-div]');if(el)toggleDiv(el.dataset.div,e.shiftKey||e.altKey);});
    on('f-reset','click',resetFilters);
    on('pmap-active-filters','click',e=>{const el=e.target.closest('[data-clear-filter]');if(el)clearFilter(el.dataset.clearFilter);});
    on('f-metric','change',e=>{S.metric=e.target.value;render();});
    on('f-color','change',e=>{S.color=e.target.value;render();});
    on('p-q','input',()=>{if(S.data)renderPeople();});
    on('p-clear','click',()=>selectPerson(''));
    on('pmap-people','click',e=>{if(e.target.closest('a[data-go]'))return;const el=e.target.closest('[data-person]');if(el)selectPerson(el.dataset.person);});
    on('pmap-unloc','click',e=>{const el=e.target.closest('[data-open]');if(el)select(el.dataset.open);});
    ['pmap-people','pmap-unloc'].forEach(id=>on(id,'keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[role=button]')){e.preventDefault();e.target.click();}}));
    on('sites-in-view','change',e=>{S.inView=e.target.checked;S.listLimit=40;PCA.setPref('map-in-view',S.inView);renderSites();});
    on('sites-sort','change',e=>{S.listSort=e.target.value;PCA.setPref('map-sort',S.listSort);renderSites();});
    $('sites-in-view').checked=S.inView; $('sites-sort').value=S.listSort;
    on('sites-more','click',()=>{S.listLimit+=40;renderSites();});
    on('pmap-sites','click',e=>{const el=e.target.closest('[data-site]');if(el)select(el.dataset.site,true);});
    on('pmap-sites','mouseover',e=>{const el=e.target.closest('[data-site]');const pl=el&&S.placed.get(el.dataset.site);if(pl)hoverGroup(pl.group,pl.c,el.dataset.site);});
    on('pmap-sites','mouseleave',()=>hoverGroup(null));
    on('pmap-sites','focusin',e=>{const el=e.target.closest('[data-site]');const pl=el&&S.placed.get(el.dataset.site);if(pl)hoverGroup(pl.group,pl.c,el.dataset.site);});
    document.querySelector('.pmap-tabs').addEventListener('click',e=>{const el=e.target.closest('[data-tab]');if(el)setTab(el.dataset.tab);});
    document.querySelector('.pmap-tabs').addEventListener('keydown',e=>{const keys=['ArrowLeft','ArrowRight','Home','End'];if(!keys.includes(e.key))return;e.preventDefault();const tabs=[...document.querySelectorAll('[data-tab]')],idx=tabs.findIndex(el=>el.dataset.tab===S.tab);const next=e.key==='Home'?0:e.key==='End'?2:(idx+(e.key==='ArrowRight'?1:2))%3;setTab(tabs[next].dataset.tab);tabs[next].focus();});
    on('explorer-toggle','click',()=>setExplorer(!$('pmap').classList.contains('explorer-minimized')));
    on('map-focus','click',async()=>{
      try{if(document.fullscreenElement)await document.exitFullscreen();else if($('pmap').requestFullscreen)await $('pmap').requestFullscreen();else throw new Error('unavailable');}
      catch(e){document.body.classList.toggle('pmap-focus-mode');S.map?.resize();}
      const active=!!document.fullscreenElement||document.body.classList.contains('pmap-focus-mode');$('map-focus').setAttribute('aria-label',active?'Exit expanded map':'Expand map');
    });
    document.addEventListener('fullscreenchange',()=>{$('map-focus').setAttribute('aria-label',document.fullscreenElement?'Exit expanded map':'Expand map');S.map?.resize();});
    const mobileNav=window.matchMedia('(max-width:700px)');
    const syncNav=()=>{document.querySelector('.sidebar').inert=mobileNav.matches&&!document.body.classList.contains('map-nav-open');};
    mobileNav.addEventListener('change',syncNav);syncNav();
    on('map-navigation','click',()=>setMobileNav(!document.body.classList.contains('map-nav-open')));
    on('map-nav-scrim','click',()=>setMobileNav(false));
    on('map-orbit','click',toggleOrbit);
    $('map-orbit').disabled=reducedMotion;
    if(reducedMotion)$('map-orbit').title='Orbit is disabled by your reduced-motion preference';
    document.addEventListener('visibilitychange',()=>{if(document.hidden){stopOrbit();if(S.tl.timer)tlPause();}});
    on('map-theme','click',()=>setBasemap(S.styleKey==='dark'?'positron':'dark'));
    on('tl-play','click',()=>{if(S.tl.timer)tlPause();else tlStart();});
    on('tl-stop','click',tlStop);
    on('tl-speed','click',()=>{const speeds=[700,350,1400],idx=speeds.indexOf(+$('tl-speed').value),value=speeds[(idx+1)%speeds.length];$('tl-speed').value=String(value);$('tl-speed').textContent=value===700?'1×':value===350?'2×':'½×';});
    on('tl-scrub','input',e=>{if(!S.data)return;tlPause();S.tl.on=true;$('tl-stop').hidden=false;S.tl.idx=+e.target.value;tlShow();});
    document.querySelector('.pmap-toolbar').addEventListener('click',e=>{
      const b=e.target.closest('[data-view]');if(!b||!S.map)return;stopOrbit();
      if(b.dataset.view==='chicago'||b.dataset.view==='loop'){closeDetail();S.map.flyTo({...(b.dataset.view==='chicago'?CHICAGO:LOOP),offset:[$('pmap').classList.contains('explorer-minimized')?0:130,-30],duration:duration(1200)});}
      else if(b.dataset.view==='top')S.map.easeTo({pitch:S.map.getPitch()>5?0:55,bearing:S.map.getPitch()>5?0:-12,duration:duration(700)});
      else if(b.dataset.view==='fit'){closeDetail();fitTo(S.visible.map(p=>[p.lng,p.lat]));}
    });
    on('pmap-detail','click',e=>{
      if(e.target.closest('[data-close]')){closeDetail();$('tab-sites').focus();}
      else if(e.target.id==='loc-save')locate('');
      else if(e.target.id==='loc-reset')locate('reset');
      else if(e.target.closest('[data-site]'))select(e.target.closest('[data-site]').dataset.site,true);
      else if(e.target.closest('[data-zoom-site]'))flyToSite(byId(e.target.closest('[data-zoom-site]').dataset.zoomSite));
      else if(e.target.closest('[data-zoom-group]')&&S.openGroup){const pts=S.openGroup.map(id=>byId(id)).filter(Boolean).map(p=>[p.lng,p.lat]);closeDetail();fitTo(pts);}
    });
    on('map-empty','click',e=>{if(e.target.closest('[data-reset]'))resetFilters();});
    on('pmap-tl-banner','click',e=>{if(e.target.closest('[data-person-clear]')){e.preventDefault();selectPerson('');}});
    document.addEventListener('keydown',e=>{
      if(e.key==='/'&&!e.metaKey&&!e.ctrlKey&&!/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)&&!e.target.isContentEditable){e.preventDefault();setExplorer(false);$('f-q').focus();}
      if(e.key==='Escape'){if(document.body.classList.contains('map-nav-open'))setMobileNav(false);stopOrbit();if(!$('pmap-detail').hidden){closeDetail();$('tab-sites').focus();}hoverGroup(null);document.body.classList.remove('pmap-focus-mode');}
    });
  }

  // ------------------------------------------------------------------ boot
  readUrl();
  const w = $('f-w'); w.value = ['week', 'month', '90d', 'year', 'all'].includes(CFG.window) ? CFG.window : 'custom';
  if (w.value === 'custom') { $('f-custom').hidden = false; const m = CFG.window.match(/^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$/); if (m) { $('f-from').value = m[1]; $('f-to').value = m[2]; } }
  wire();
  initMap();
  load();
  window.PMAP = {S, select, selectPerson, tlStart, tlStop, render, setBasemap, hitAt, openGroup};   // debug / test handle
})();
