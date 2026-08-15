/* Designer (Bèta) - Canva-achtige editor in PLUS-huisstijl.
   Elementen bewaren fractionele coördinaten (0..1 van het canvas). Meerdere pagina's, undo/redo,
   slimme uitlijn-hulplijnen + snapping, zoom, PLUS-productzoek, sneltoetsen. Server rendert print-
   perfect met dezelfde fonts. */
(function () {
  var I = window.DZ_INIT;
  var PXMM = 96 / 25.4;                                  // basis: 96 dpi
  // PLUS-blaadjes laadspinner (op de plek van een nog-ladend element)
  var PL_SPIN = '<svg class="el-leaves" viewBox="0 0 132 140"><path class="el-leaf el-leaf--1" d="M61.2,32.8 L61.2,54.8 C61.2,60.8 56.8,65.6 50.8,65.6 L30.4,65.6 C13.6,65.6 0,51.2 0,33.2 C0,15.6 13.6,0.8 30.4,0.8 C47.6,0.8 61.2,15.2 61.2,32.8Z"/><path class="el-leaf el-leaf--2" d="M61.2,107.2 L61.2,85.6 C61.2,79.6 56.8,74.8 50.8,74.8 L30.4,74.8 C13.6,74.8 0,89.2 0,107.2 C0,125.2 13.6,139.6 30.4,139.6 C47.6,139.6 61.2,125.2 61.2,107.2Z"/><path class="el-leaf el-leaf--3" d="M69.6,107.2 L69.6,85.6 C69.6,79.6 74,74.8 80,74.8 L100.4,74.8 C117.2,74.8 131.2,89.2 131.2,107.2 C131.2,125.2 117.2,139.6 100.4,139.6 C83.6,139.6 69.6,125.2 69.6,107.2Z"/><path class="el-leaf el-leaf--4" d="M70,32.8 L70,54.8 C70,60.8 74.4,65.6 80,65.6 L100.4,65.6 C117.6,65.6 131.2,51.2 131.2,33.2 C131.2,15.6 117.6,0.8 100.4,0.8 C83.6,0.8 70,15.2 70,32.8Z"/></svg>';
  // Scherpe versie van een plus.nl/ctfassets-productfoto (thumbnail → volledige kwaliteit)
  function hiRes(u) { try { if (/ctfassets\.net|\.plus\.nl/i.test(u)) { var url = new URL(u, location.href); url.searchParams.set('w', '1000'); url.searchParams.set('q', '90'); url.searchParams.delete('h'); return url.toString(); } } catch (e) { } return u; }
  // --- State (met migratie van oud enkel-pagina-formaat) ---
  var state = { pages: [], cur: 0 };
  if (I.data && Array.isArray(I.data.pages) && I.data.pages.length) state.pages = I.data.pages;
  else state.pages = [{ bg: (I.data && I.data.bg) || '#ffffff', elements: (I.data && I.data.elements) || [] }];
  state.pages.forEach(function (p) { (p.elements || (p.elements = [])).forEach(function (e) { if (!e.id) e.id = uid(); }); });

  var stage, wrap, canvasWrap, panel, props, rail, guides, titleEl, saveState, zoomLbl, pagesBar;
  var selIds = [], zoom = 1, uidc = 1, saveTimer = null;
  var hist = [], hi = -1;                                // undo-stack

  function uid() { return 'e' + (uidc++) + '_' + Math.random().toString(36).slice(2, 6); }
  function $(s, r) { return (r || document).querySelector(s); }
  function page() { return state.pages[state.cur]; }
  function els() { return page().elements; }
  function el(id) { return els().find(function (e) { return e.id === id; }); }
  function sel() { return selIds.length === 1 ? el(selIds[0]) : null; }
  function fontFam(f) { return f === 'gothic' ? 'GothicA1' : 'Montserrat'; }
  function baseH() { return stage.offsetHeight; }
  function baseW() { return stage.offsetWidth; }
  function rect() { return stage.getBoundingClientRect(); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

  // ---- Zoom / stage ----------------------------------------------------------
  function setStageBase() {
    stage.style.width = Math.round(I.w_mm * PXMM) + 'px';
    stage.style.height = Math.round(I.h_mm * PXMM) + 'px';
    guides.setAttribute('viewBox', '0 0 ' + Math.round(I.w_mm * PXMM) + ' ' + Math.round(I.h_mm * PXMM));
    guides.style.width = stage.style.width; guides.style.height = stage.style.height;
  }
  function applyZoom() {
    canvasWrap.style.transform = 'scale(' + zoom + ')';
    var sw = baseW() * zoom, sh = baseH() * zoom;
    var pad = 40;
    canvasWrap.style.left = Math.max(pad, (wrap.clientWidth - sw) / 2) + 'px';
    canvasWrap.style.top = Math.max(pad, (wrap.clientHeight - sh) / 2) + 'px';
    // scroll-ruimte
    stage.parentElement.style.width = (baseW()) + 'px';
    wrap.style.setProperty('--sw', sw + 'px');
    zoomLbl.textContent = Math.round(zoom * 100) + '%';
  }
  function zoomFit() {
    zoom = Math.min((wrap.clientWidth - 80) / baseW(), (wrap.clientHeight - 80) / baseH());
    zoom = clamp(zoom, .05, 3); applyZoom();
  }
  function zoomBy(d) { zoom = clamp(zoom + d, .1, 5); applyZoom(); }

  // ---- Render ----------------------------------------------------------------
  function render() {
    var pg = page();
    stage.style.background = pg.bg || '#ffffff';
    stage.innerHTML = '';
    pg.elements.slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0); }).forEach(function (e) {
      var d = document.createElement('div');
      d.className = 'el' + (selIds.indexOf(e.id) > -1 ? ' sel' : '') + (e.lock ? ' locked' : '');
      d.dataset.id = e.id;
      d.style.left = (e.x * 100) + '%'; d.style.top = (e.y * 100) + '%';
      d.style.width = (e.w * 100) + '%'; d.style.height = (e.h * 100) + '%';
      d.style.transform = 'rotate(' + (e.rot || 0) + 'deg)';
      d.style.opacity = (e.op == null ? 1 : e.op);
      d.style.zIndex = (e.z || 0);
      d.innerHTML = content(e);
      var im = d.querySelector('img[data-imgel]');
      if (im) { var show = function () { im.style.opacity = 1; var sp = d.querySelector('.el-spin'); if (sp) sp.style.display = 'none'; }; var hide = function () { var sp = d.querySelector('.el-spin'); if (sp) sp.style.display = 'none'; }; if (im.complete && im.naturalWidth) show(); else { im.onload = show; im.onerror = hide; } }
      d.addEventListener('pointerdown', function (ev) { startMove(ev, e); });
      if (e.type === 'text') d.addEventListener('dblclick', function () { editText(e, d); });
      if (e.type === 'table') d.addEventListener('dblclick', function (ev) { editTable(e, d, ev); });
      if (selIds.length === 1 && selIds[0] === e.id && !e.lock) addHandles(d, e);
      stage.appendChild(d);
    });
    paintText();
  }
  function content(e) {
    if (e.type === 'text') {
      var st = 'font-family:' + fontFam(e.font) + ';font-weight:' + (e.bold ? 700 : 400) +
        ';font-style:' + (e.italic ? 'italic' : 'normal') + ';color:' + e.color +
        ';text-align:' + e.align + ';font-size:' + (e.size * baseH()) + 'px;line-height:' + (e.lineh || 1.15) +
        ';letter-spacing:' + ((e.ls || 0) * baseH()) + 'px;justify-content:' + (e.valign === 'top' ? 'flex-start' : 'center') + ';';
      return '<div class="etext" style="' + st + '"></div>';
    }
    if (e.type === 'image' || e.type === 'icon') return '<div class="el-imgwrap"><div class="el-spin">' + PL_SPIN + '</div><img data-imgel="1" src="' + (e.src || e.url || '') + '" style="object-fit:' + (e.fit || 'contain') + ';opacity:0"></div>';
    if (e.type === 'barcode') return '<img class="ebc" src="' + I.barcodeUrl + '?value=' + encodeURIComponent(e.value || '') + '&showtext=' + (e.showText ? 1 : 0) + '">';
    if (e.type === 'shape') return shape(e);
    if (e.type === 'table') return tableHTML(e);
    return '';
  }
  function shape(e) {
    var s = 'width:100%;height:100%;box-sizing:border-box;';
    if (e.shape === 'line') return '<div class="eshape" style="' + s + 'display:flex;align-items:center;"><div style="width:100%;height:' + (e.strokeW || 3) + 'px;background:' + (e.stroke !== 'none' ? e.stroke : e.fill) + ';"></div></div>';
    if (e.shape === 'arrow') return '<div class="eshape" style="' + s + 'color:' + (e.stroke !== 'none' ? e.stroke : e.fill) + ';display:flex;align-items:center;justify-content:center;font-size:' + Math.max(10, e.h * baseH() * .9) + 'px;"><i class="fa fa-arrow-right-long"></i></div>';
    if (e.shape === 'triangle' || e.shape === 'star') {
      var clip = e.shape === 'triangle' ? 'polygon(50% 0,100% 100%,0 100%)' : 'polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%)';
      return '<div class="eshape" style="' + s + 'background:' + (e.fill !== 'none' ? e.fill : 'transparent') + ';-webkit-clip-path:' + clip + ';clip-path:' + clip + ';"></div>';
    }
    s += 'background:' + (e.fill && e.fill !== 'none' ? e.fill : 'transparent') + ';';
    if (e.stroke && e.stroke !== 'none') s += 'border:' + (e.strokeW || 2) + 'px solid ' + e.stroke + ';';
    s += 'border-radius:' + (e.shape === 'ellipse' ? '50%' : ((e.radius || 0) * 100 + '%')) + ';';
    return '<div class="eshape" style="' + s + '"></div>';
  }
  function paintText() { els().forEach(function (e) { if (e.type === 'text') { var n = $('.el[data-id="' + e.id + '"] .etext'); if (n) n.textContent = e.text || ''; } }); }

  function addHandles(d, e) {
    ['nw', 'ne', 'sw', 'se'].forEach(function (p) { var h = document.createElement('div'); h.className = 'handle ' + p; h.addEventListener('pointerdown', function (ev) { startResize(ev, e, p); }); d.appendChild(h); });
    var r = document.createElement('div'); r.className = 'handle rot'; r.title = 'Draaien (Shift = per 15°)'; r.innerHTML = '<i class="fa fa-rotate"></i>';
    r.addEventListener('pointerdown', function (ev) { startRotate(ev, e); }); d.appendChild(r);
  }

  // ---- Interacties + snapping ------------------------------------------------
  function startMove(ev, e) {
    ev.stopPropagation();
    if (ev.target.classList.contains('handle')) return;
    if (window._dzEditing === e.id) return;              // tijdens tabel-bewerken niet slepen
    if (e.lock) { select(e.id); return; }
    if (ev.shiftKey) { toggleSel(e.id); return; }
    if (selIds.indexOf(e.id) < 0) select(e.id);
    var moving = selIds.slice(); var r = rect(), sx = ev.clientX, sy = ev.clientY;
    var start = {}; moving.forEach(function (id) { var x = el(id); start[id] = { x: x.x, y: x.y }; });
    var moved = false;
    function mv(m) {
      moved = true;
      var dx = (m.clientX - sx) / r.width, dy = (m.clientY - sy) / r.height;
      moving.forEach(function (id) { var x = el(id); x.x = start[id].x + dx; x.y = start[id].y + dy; });
      var snap = (moving.length === 1 && !m.altKey) ? doSnap(el(moving[0])) : { gx: [], gy: [] };
      moving.forEach(function (id) { var x = el(id), n = $('.el[data-id="' + id + '"]'); if (n) { n.style.left = (x.x * 100) + '%'; n.style.top = (x.y * 100) + '%'; } });
      drawGuides(snap);
    }
    drag(mv, function () { clearGuides(); if (moved) { buildProps(); pushHist(); scheduleSave(); } });
  }
  function doSnap(e) {
    var th = 6 / rect().width * baseW();               // drempel in basis-px
    var W = baseW(), H = baseH();
    var mx = (e.x + e.w / 2) * W, my = (e.y + e.h / 2) * H;
    var lx = e.x * W, rx = (e.x + e.w) * W, ty = e.y * H, by = (e.y + e.h) * H;
    var vt = [{ p: W / 2 }, { p: 0 }, { p: W }], ht = [{ p: H / 2 }, { p: 0 }, { p: H }];
    els().forEach(function (o) { if (o.id === e.id) return; vt.push({ p: (o.x + o.w / 2) * W }, { p: o.x * W }, { p: (o.x + o.w) * W }); ht.push({ p: (o.y + o.h / 2) * H }, { p: o.y * H }, { p: (o.y + o.h) * H }); });
    var gx = [], gy = [];
    // verticaal (x): probeer center/left/right
    [[mx, e.w / 2 * W], [lx, 0], [rx, e.w * W]].some(function (pair) {
      var best = null; vt.forEach(function (t) { if (Math.abs(pair[0] - t.p) < th && (!best || Math.abs(pair[0] - t.p) < Math.abs(pair[0] - best.p))) best = t; });
      if (best) { e.x = (best.p - pair[1]) / W - (pair[0] === mx ? 0 : 0); e.x = (best.p - pair[1] * (pair[0] === mx ? 1 : 1)) / W; gx.push(best.p); return true; }
      return false;
    });
    [[my, e.h / 2 * H], [ty, 0], [by, e.h * H]].some(function (pair) {
      var best = null; ht.forEach(function (t) { if (Math.abs(pair[0] - t.p) < th && (!best || Math.abs(pair[0] - t.p) < Math.abs(pair[0] - best.p))) best = t; });
      if (best) { e.y = (best.p - pair[1]) / H; gy.push(best.p); return true; }
      return false;
    });
    return { gx: gx, gy: gy };
  }
  function drawGuides(s) {
    var H = baseH(), W = baseW(); var out = '';
    (s.gx || []).forEach(function (x) { out += '<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + H + '" stroke="#dd350d" stroke-width="1"/>'; });
    (s.gy || []).forEach(function (y) { out += '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="#dd350d" stroke-width="1"/>'; });
    guides.innerHTML = out;
  }
  function clearGuides() { guides.innerHTML = ''; }
  function startResize(ev, e, p) {
    ev.stopPropagation();
    var r = rect(), sx = ev.clientX, sy = ev.clientY, ox = e.x, oy = e.y, ow = e.w, oh = e.h;
    function mv(m) {
      var dx = (m.clientX - sx) / r.width, dy = (m.clientY - sy) / r.height;
      if (p.indexOf('e') > -1) e.w = Math.max(.02, ow + dx);
      if (p.indexOf('s') > -1) e.h = Math.max(.02, oh + dy);
      if (p.indexOf('w') > -1) { e.w = Math.max(.02, ow - dx); e.x = ox + dx; }
      if (p.indexOf('n') > -1) { e.h = Math.max(.02, oh - dy); e.y = oy + dy; }
      var n = $('.el[data-id="' + e.id + '"]'); if (n) { n.style.left = (e.x * 100) + '%'; n.style.top = (e.y * 100) + '%'; n.style.width = (e.w * 100) + '%'; n.style.height = (e.h * 100) + '%'; var tx = n.querySelector('.etext'); if (tx) tx.style.fontSize = (e.size * baseH()) + 'px'; }
    }
    drag(mv, function () { buildProps(); pushHist(); scheduleSave(); });
  }
  function startRotate(ev, e) {
    ev.stopPropagation();
    var r = rect(), cx = r.left + (e.x + e.w / 2) * r.width, cy = r.top + (e.y + e.h / 2) * r.height;
    function mv(m) { var a = Math.atan2(m.clientY - cy, m.clientX - cx) * 180 / Math.PI + 90; if (m.shiftKey) a = Math.round(a / 15) * 15; e.rot = Math.round(a); var n = $('.el[data-id="' + e.id + '"]'); if (n) n.style.transform = 'rotate(' + e.rot + 'deg)'; }
    drag(mv, function () { buildProps(); pushHist(); scheduleSave(); });
  }
  function drag(mv, done) { function up() { document.removeEventListener('pointermove', mv); document.removeEventListener('pointerup', up); done && done(); } document.addEventListener('pointermove', mv); document.addEventListener('pointerup', up); }

  // ---- Selectie / toevoegen --------------------------------------------------
  function select(id) { selIds = id ? [id] : []; render(); buildProps(); }
  function toggleSel(id) { var i = selIds.indexOf(id); if (i > -1) selIds.splice(i, 1); else selIds.push(id); render(); buildProps(); }
  function center(w, h) { return { x: (1 - w) / 2, y: (1 - h) / 2, w: w, h: h, rot: 0, op: 1 }; }
  function add(e, pos) { e.id = uid(); e.z = els().length + 1; if (pos) { e.x = clamp(pos.x - e.w / 2, -0.4, 1); e.y = clamp(pos.y - e.h / 2, -0.4, 1); } els().push(e); select(e.id); pushHist(); scheduleSave(); }

  var DZ = {
    addText: function (kind, pos) {
      var pre = { kop: { size: .075, bold: true, text: 'Kop' }, sub: { size: .045, bold: true, text: 'Subkop' }, body: { size: .028, bold: false, text: 'Bodytekst' } }[kind] || { size: .05, bold: true, text: 'Tekst' };
      var c = center(.55, pre.size * 1.6); c.type = 'text'; c.text = pre.text; c.font = 'montserrat'; c.size = pre.size; c.color = '#231f20'; c.align = 'center'; c.bold = pre.bold; c.italic = false; c.lineh = 1.15; c.ls = 0; c.valign = 'middle'; add(c, pos);
    },
    addBarcode: function (pos) { var c = center(.5, .16); c.type = 'barcode'; c.value = '8710400015208'; c.showText = true; add(c, pos); },
    addShape: function (s, pos) { var c = center(s === 'line' || s === 'arrow' ? .35 : .28, s === 'line' ? .03 : .28); c.type = 'shape'; c.shape = s; c.fill = (s === 'line' || s === 'arrow') ? 'none' : '#80bd1d'; c.stroke = (s === 'line' || s === 'arrow') ? '#231f20' : 'none'; c.strokeW = 4; c.radius = 0; add(c, pos); },
    addIcon: function (name, pos) { rasterIcon(name, '#80bd1d').then(function (src) { var c = center(.16, .16); c.type = 'icon'; c.icon = name; c.color = '#80bd1d'; c.src = src; c.fit = 'contain'; add(c, pos); }); },
    addMaterial: function (name, pos) { rasterMaterial(name, '#231f20').then(function (src) { if (!src) return; var c = center(.16, .16); c.type = 'icon'; c.msicon = name; c.color = '#231f20'; c.src = src; c.fit = 'contain'; add(c, pos); }); },
    addImage: function (src, url, pos) { var img = new Image(); var mk = function (w, h) { var c = center(w, h); c.type = 'image'; if (url) c.url = url; else c.src = src; c.fit = 'contain'; add(c, pos); }; img.onload = function () { var ar = img.width / img.height, w = .5, h = w / ar / (I.w_mm / I.h_mm); if (h > .8) { h = .8; w = h * ar * (I.w_mm / I.h_mm); } mk(w, h); }; img.onerror = function () { mk(.4, .4); }; img.src = src || url; },
    addTable: function (pos) { var c = center(.6, .3); c.type = 'table'; c.rows = 3; c.cols = 3; c.cells = [['Kop 1', 'Kop 2', 'Kop 3'], ['', '', ''], ['', '', '']]; c.header = true; c.border = '#d8d8d8'; c.headerBg = '#eef6e1'; c.color = '#231f20'; c.size = .026; add(c, pos); },
    setBg: function (col) { page().bg = col; stage.style.background = col; $('#dzBgColor').value = col; pushHist(); scheduleSave(); },
    del: function () { if (!selIds.length) return; page().elements = els().filter(function (e) { return selIds.indexOf(e.id) < 0; }); selIds = []; render(); buildProps(); pushHist(); scheduleSave(); },
    dup: function () { var e = sel(); if (!e) return; var n = JSON.parse(JSON.stringify(e)); n.x += .02; n.y += .02; add(n); },
    layer: function (dir) { var e = sel(); if (!e) return; if (dir === 'front') e.z = els().length + 2; else if (dir === 'back') e.z = -1; else e.z = (e.z || 0) + (dir === 'up' ? 1.5 : -1.5); normZ(); render(); scheduleSave(); pushHist(); },
    lock: function () { var e = sel(); if (!e) return; e.lock = !e.lock; render(); buildProps(); scheduleSave(); },
    undo: undo, redo: redo, zoomBy: zoomBy, zoomFit: zoomFit,
    save: save, preview: preview, printLabel: printLabel,
    plusSearch: plusSearch, plusAdd: plusAdd, msSearch: msSearch,
    addPage: addPage, dupPage: dupPage, delPage: delPage, goPage: goPage
  };
  window.DZ = DZ;
  function normZ() { els().slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0); }).forEach(function (e, i) { e.z = i + 1; }); }

  // ---- Undo / redo -----------------------------------------------------------
  function snap() { return JSON.stringify({ pages: state.pages }); }
  function pushHist() { hist = hist.slice(0, hi + 1); hist.push(snap()); if (hist.length > 60) hist.shift(); hi = hist.length - 1; updUndo(); }
  function updUndo() { $('#dzUndo').disabled = hi <= 0; $('#dzRedo').disabled = hi >= hist.length - 1; }
  function restore(s) { var o = JSON.parse(s); state.pages = o.pages; if (state.cur >= state.pages.length) state.cur = state.pages.length - 1; selIds = []; render(); buildProps(); renderPages(); scheduleSave(); }
  function undo() { if (hi > 0) { hi--; restore(hist[hi]); updUndo(); } }
  function redo() { if (hi < hist.length - 1) { hi++; restore(hist[hi]); updUndo(); } }

  // ---- Icoon → afbeelding ----------------------------------------------------
  function rasterIcon(name, color) {
    return new Promise(function (res) {
      var pr = document.createElement('i'); pr.className = 'fa fa-' + name; pr.style.cssText = 'position:absolute;left:-9999px;font-weight:900;'; document.body.appendChild(pr);
      var ch = getComputedStyle(pr, '::before').content.replace(/["']/g, ''); var fam = getComputedStyle(pr).fontFamily || '"Font Awesome 6 Free"'; document.body.removeChild(pr);
      function draw() { var sz = 256, c = document.createElement('canvas'); c.width = c.height = sz; var x = c.getContext('2d'); x.fillStyle = color; x.font = '900 ' + Math.floor(sz * .82) + 'px ' + fam; x.textAlign = 'center'; x.textBaseline = 'middle'; x.fillText(ch || '?', sz / 2, sz / 2 + sz * .04); res(c.toDataURL('image/png')); }
      if (document.fonts && document.fonts.load) document.fonts.load('900 40px ' + fam).then(draw, draw); else draw();
    });
  }
  function editText(e, d) { var n = d.querySelector('.etext'); if (!n) return; n.setAttribute('contenteditable', 'true'); n.style.cursor = 'text'; n.focus(); document.execCommand && document.execCommand('selectAll', false, null); function done() { e.text = n.innerText; n.removeAttribute('contenteditable'); n.removeEventListener('blur', done); buildProps(); pushHist(); scheduleSave(); } n.addEventListener('blur', done); }

  // ---- Eigenschappen-paneel --------------------------------------------------
  function buildProps() {
    var e = sel();
    if (!e) { props.innerHTML = selIds.length > 1 ? multiProps() : '<div class="dz-empty"><i class="fa fa-hand-pointer fa-2x" style="color:#cfcfcf"></i><br><br>Selecteer een element om het aan te passen.</div>'; if (selIds.length > 1) bindMulti(); return; }
    var h = '<div class="dz-btnrow" style="margin-bottom:12px;">' +
      '<button class="dz-mini" title="Dupliceren" onclick="DZ.dup()"><i class="fa fa-clone"></i></button>' +
      '<button class="dz-mini" title="Naar voren" onclick="DZ.layer(\'up\')"><i class="fa fa-arrow-up"></i></button>' +
      '<button class="dz-mini" title="Naar achter" onclick="DZ.layer(\'down\')"><i class="fa fa-arrow-down"></i></button>' +
      '<button class="dz-mini ' + (e.lock ? 'on' : '') + '" title="Vergrendelen" onclick="DZ.lock()"><i class="fa fa-lock"></i></button>' +
      '<button class="dz-mini" title="Verwijderen" onclick="DZ.del()" style="color:var(--red)"><i class="fa fa-trash"></i></button></div>';
    if (e.type === 'text') {
      h += '<h4>Tekst</h4>' + f('textarea', 'text', 'Inhoud', e.text) +
        '<div class="dz-row">' + fsel('font', 'Lettertype', e.font, I.fonts) + f('number', 'size100', 'Grootte', Math.round(e.size * 100)) + '</div>' +
        '<div class="dz-row">' + f('color', 'color', 'Kleur', e.color) + fsel('align', 'Uitlijnen', e.align, [['left', 'Links'], ['center', 'Midden'], ['right', 'Rechts']]) + '</div>' +
        '<div class="dz-row">' + f('number', 'lineh', 'Regelafstand', e.lineh || 1.15) + f('number', 'ls100', 'Letters', Math.round((e.ls || 0) * 100)) + '</div>' +
        '<div class="dz-btnrow"><button class="dz-mini ' + (e.bold ? 'on' : '') + '" onclick="DZ_tog(\'bold\')"><b>B</b></button><button class="dz-mini ' + (e.italic ? 'on' : '') + '" onclick="DZ_tog(\'italic\')"><i>I</i></button></div>';
    } else if (e.type === 'barcode') {
      h += '<h4>Barcode</h4>' + f('text', 'value', 'Code', e.value) + '<div class="dz-btnrow"><button class="dz-mini ' + (e.showText ? 'on' : '') + '" onclick="DZ_tog(\'showText\')">Cijfers tonen</button></div>';
    } else if (e.type === 'shape') {
      h += '<h4>Vorm</h4>';
      if (['rect', 'ellipse', 'triangle', 'star'].indexOf(e.shape) > -1) h += '<div class="dz-row">' + f('color', 'fill', 'Vulkleur', e.fill === 'none' ? '#ffffff' : e.fill) + '<div class="dz-f"><label>Vullen</label><button class="dz-mini ' + (e.fill !== 'none' ? 'on' : '') + '" onclick="DZ_togFill()">' + (e.fill !== 'none' ? 'Aan' : 'Uit') + '</button></div></div>';
      h += '<div class="dz-row">' + f('color', 'stroke', e.shape === 'line' || e.shape === 'arrow' ? 'Kleur' : 'Rand', e.stroke === 'none' ? '#231f20' : e.stroke) + f('number', 'strokeW', 'Dikte', e.strokeW) + '</div>';
      if (e.shape === 'rect') h += f('number', 'radius100', 'Ronding %', Math.round((e.radius || 0) * 100));
    } else if (e.type === 'icon') { h += '<h4>Icoon</h4>' + f('color', 'iconcolor', 'Kleur', e.color || '#80bd1d');
    } else if (e.type === 'image') { h += '<h4>Foto</h4>' + fsel('fit', 'Passend', e.fit || 'contain', [['contain', 'Passen'], ['cover', 'Vullen'], ['fill', 'Uitrekken']]);
    } else if (e.type === 'table') { h += '<h4>Tabel</h4><div class="dz-row">' + f('number', 'rows', 'Rijen', e.rows) + f('number', 'cols', 'Kolommen', e.cols) + '</div>' +
        '<div class="dz-row">' + f('color', 'color', 'Tekst', e.color) + f('number', 'size100', 'Grootte', Math.round(e.size * 100)) + '</div>' +
        '<div class="dz-row">' + f('color', 'border', 'Lijnen', e.border) + f('color', 'headerBg', 'Kopkleur', e.headerBg) + '</div>' +
        '<div class="dz-btnrow"><button class="dz-mini ' + (e.header ? 'on' : '') + '" onclick="DZ_tog(\'header\')">Koprij</button></div>' +
        '<p class="hint" style="margin:6px 0 0;">Dubbelklik de tabel om cellen te bewerken.</p>'; }
    h += '<h4>Positie &amp; laag</h4>' +
      '<div class="dz-row">' + f('number', 'rot', 'Draaien °', Math.round(e.rot || 0)) + '<div class="dz-f"><label>Transparant.</label><input type="range" min="0" max="100" data-k="op100" value="' + Math.round((e.op == null ? 1 : e.op) * 100) + '"></div></div>' +
      '<div class="dz-btnrow"><button class="dz-mini" onclick="DZ.layer(\'front\')">Naar voorgrond</button><button class="dz-mini" onclick="DZ.layer(\'back\')">Naar achtergrond</button></div>';
    props.innerHTML = h; bindProps(e);
  }
  function multiProps() {
    return '<div class="dz-empty">' + selIds.length + ' elementen geselecteerd</div>' +
      '<div class="dz-btnrow"><button class="dz-mini" onclick="DZ.dup()"><i class="fa fa-clone"></i></button><button class="dz-mini" onclick="DZ.del()" style="color:var(--red)"><i class="fa fa-trash"></i></button></div>';
  }
  function bindMulti() { }
  function f(type, key, label, val) {
    if (type === 'textarea') return '<div class="dz-f" style="width:100%"><label>' + label + '</label><textarea rows="2" data-k="' + key + '">' + (val || '') + '</textarea></div>';
    return '<div class="dz-f"><label>' + label + '</label><input type="' + type + '" step="any" data-k="' + key + '" value="' + (val == null ? '' : String(val).replace(/"/g, '&quot;')) + '"></div>';
  }
  function fsel(key, label, val, opts) { return '<div class="dz-f"><label>' + label + '</label><select data-k="' + key + '">' + opts.map(function (o) { return '<option value="' + o[0] + '"' + (o[0] === val ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select></div>'; }
  function bindProps(e) {
    props.querySelectorAll('[data-k]').forEach(function (inp) {
      var ev = (inp.tagName === 'SELECT' || inp.type === 'color') ? 'change' : 'input';
      inp.addEventListener(ev, function () {
        var k = inp.dataset.k, v = inp.value;
        if (k === 'size100') e.size = Math.max(1, +v) / 100;
        else if (k === 'ls100') e.ls = (+v) / 100;
        else if (k === 'radius100') e.radius = Math.max(0, +v) / 100;
        else if (k === 'op100') { e.op = (+v) / 100; var n = $('.el[data-id="' + e.id + '"]'); if (n) n.style.opacity = e.op; scheduleSave(); return; }
        else if (k === 'rows' || k === 'cols') { e[k] = Math.max(1, Math.min(20, +v || 1)); fixTableCells(e); }
        else if (k === 'strokeW' || k === 'rot' || k === 'lineh') e[k] = +v;
        else if (k === 'iconcolor') { e.color = v; rasterIcon(e.icon, v).then(function (src) { e.src = src; render(); }); scheduleSave(); return; }
        else e[k] = v;
        render(); scheduleSave();
      });
      if (inp.dataset.k === 'op100' || inp.dataset.k === 'rot') inp.addEventListener('change', function () { pushHist(); });
    });
  }
  window.DZ_tog = function (k) { var e = sel(); if (!e) return; e[k] = !e[k]; render(); buildProps(); pushHist(); scheduleSave(); };
  window.DZ_togFill = function () { var e = sel(); if (!e) return; e.fill = e.fill === 'none' ? '#80bd1d' : 'none'; render(); buildProps(); pushHist(); scheduleSave(); };

  // ---- PLUS-productzoek -------------------------------------------------------
  function plusSearch() {
    var q = $('#dzPlusQ').value.trim(); if (q.length < 2) return false;
    var box = $('#dzPlusRes'); box.innerHTML = '<div class="dz-empty"><i class="fa fa-spinner fa-spin"></i> Zoeken…</div>';
    fetch(I.plusUrl + '?q=' + encodeURIComponent(q), { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (list) {
      if (!Array.isArray(list) || !list.length) { box.innerHTML = '<div class="dz-empty">Geen resultaten.</div>'; return; }
      box.innerHTML = list.slice(0, 12).map(function (p, i) {
        var price = p.actie || p.prijs || ''; var pretty = price ? ('€ ' + String(price).replace('.', ',')) : '';
        window['_plus' + i] = p;
        return '<div class="dz-pr"><div class="dz-pr__top">' +
          (p.img ? '<img class="dz-pr__img" src="' + p.img + '">' : '<div class="dz-pr__img"></div>') +
          '<div><div class="dz-pr__name">' + esc(p.naam) + '</div>' + (pretty ? '<div class="dz-pr__price">' + pretty + '</div>' : '') + '</div></div>' +
          '<div class="dz-pr__opts">' +
          (p.img ? '<label><input type="checkbox" checked data-o="img"> Foto</label>' : '') +
          '<label><input type="checkbox" checked data-o="naam"> Naam</label>' +
          (pretty ? '<label><input type="checkbox" data-o="prijs"> Prijs</label>' : '') + '</div>' +
          '<button class="btn btn-primary btn-sm btn-block" onclick="DZ.plusAdd(' + i + ',this)"><i class="fa fa-plus"></i> Toevoegen</button></div>';
      }).join('');
    }).catch(function () { box.innerHTML = '<div class="dz-empty">Zoeken mislukt.</div>'; });
    return false;
  }
  function plusAdd(i, btn) {
    var p = window['_plus' + i]; if (!p) return;
    var opts = {}; btn.closest('.dz-pr').querySelectorAll('[data-o]').forEach(function (c) { opts[c.dataset.o] = c.checked; });
    var y = .3;
    if (opts.img && p.img) { DZ.addImage(null, hiRes(p.img)); y = .55; }
    if (opts.naam) { var c = center(.6, .07); c.type = 'text'; c.text = p.naam; c.font = 'montserrat'; c.size = .04; c.color = '#231f20'; c.align = 'center'; c.bold = true; c.italic = false; c.lineh = 1.1; c.valign = 'middle'; c.y = y; add(c); }
    if (opts.prijs) { var price = p.actie || p.prijs || ''; var pc = center(.4, .08); pc.type = 'text'; pc.text = '€ ' + String(price).replace('.', ','); pc.font = 'montserrat'; pc.size = .06; pc.color = '#dd350d'; pc.align = 'center'; pc.bold = true; pc.italic = false; pc.lineh = 1; pc.valign = 'middle'; pc.y = y + .1; add(pc); }
  }
  function esc(s) { return (s || '').replace(/</g, '&lt;'); }
  function fixTableCells(e) { e.cells = e.cells || []; for (var r = 0; r < e.rows; r++) { e.cells[r] = e.cells[r] || []; for (var c = 0; c < e.cols; c++) if (e.cells[r][c] == null) e.cells[r][c] = ''; e.cells[r] = e.cells[r].slice(0, e.cols); } e.cells = e.cells.slice(0, e.rows); }

  // ---- Tabel -----------------------------------------------------------------
  function tableHTML(e) {
    var fs = (e.size || .026) * baseH();
    var s = '<table style="width:100%;height:100%;border-collapse:collapse;font-family:Montserrat;font-size:' + fs + 'px;color:' + e.color + ';table-layout:fixed;pointer-events:none;">';
    for (var r = 0; r < e.rows; r++) { s += '<tr>'; for (var col = 0; col < e.cols; col++) { var head = (e.header && r === 0); var v = (e.cells[r] && e.cells[r][col]) || ''; s += '<td data-r="' + r + '" data-c="' + col + '" style="border:1px solid ' + e.border + ';padding:3px 5px;vertical-align:middle;' + (head ? 'background:' + e.headerBg + ';font-weight:800;' : '') + 'overflow:hidden;">' + esc(v).replace(/\n/g, '<br>') + '</td>'; } s += '</tr>'; }
    return s + '</table>';
  }
  function editTable(e, d, ev) {
    window._dzEditing = e.id; d.classList.add('editing');
    var tbl = d.querySelector('table'); tbl.style.pointerEvents = 'auto';
    var tds = [].slice.call(d.querySelectorAll('td'));
    tds.forEach(function (td) { td.contentEditable = 'true'; td.style.cursor = 'text'; });
    function focusCell(td) { if (!td) return; td.focus(); setTimeout(function () { try { document.getSelection().selectAllChildren(td); } catch (x) { } }, 0); }
    focusCell((ev && ev.target.closest) ? ev.target.closest('td') : tds[0]);
    function key(k) {
      if (k.key === 'Tab') { k.preventDefault(); var i = tds.indexOf(document.activeElement); focusCell(tds[i + (k.shiftKey ? -1 : 1)]); }
      else if (k.key === 'Escape') { k.preventDefault(); commit(); }
    }
    function commit() {
      tds.forEach(function (td) { var r = +td.dataset.r, c = +td.dataset.c; if (!e.cells[r]) e.cells[r] = []; e.cells[r][c] = td.innerText.replace(/ /g, ' ').replace(/\n+$/, ''); td.contentEditable = 'false'; td.style.cursor = ''; });
      tbl.style.pointerEvents = 'none'; d.classList.remove('editing'); window._dzEditing = null;
      document.removeEventListener('pointerdown', outside, true); d.removeEventListener('keydown', key);
      buildProps(); pushHist(); scheduleSave();
    }
    function outside(pe) { if (!d.contains(pe.target)) commit(); }
    d.addEventListener('keydown', key);
    setTimeout(function () { document.addEventListener('pointerdown', outside, true); }, 0);
  }

  // ---- Material Icons (zoekbaar; via ligatuur → afbeelding) -------------------
  var MS_ICONS = ('home search settings favorite favorite_border star star_border grade thumb_up thumb_down ' +
    'shopping_cart shopping_bag shopping_basket local_offer sell payments euro savings paid discount ' +
    'store storefront restaurant restaurant_menu local_dining lunch_dining dinner_dining fastfood ramen_dining ' +
    'local_pizza icecream cake bakery_dining liquor wine_bar local_bar local_cafe coffee emoji_food_beverage ' +
    'egg egg_alt set_meal kebab_dining tapas brunch_dining local_florist eco spa park forest agriculture ' +
    'nutrition grass water_drop local_fire_department whatshot bolt flash_on ac_unit thermostat sunny cloud ' +
    'calendar_today calendar_month event schedule alarm timer today date_range access_time ' +
    'call phone smartphone mail email chat sms message forum notifications campaign volume_up ' +
    'location_on place map navigation directions local_shipping local_mall delivery_dining ' +
    'person group groups face account_circle badge verified check check_circle done task_alt ' +
    'close cancel error warning info help report block add remove edit delete visibility ' +
    'thumb_up_alt recommend celebration redeem card_giftcard loyalty diamond workspace_premium ' +
    'percent trending_up trending_down bar_chart pie_chart show_chart insights leaderboard ' +
    'lightbulb tips_and_updates auto_awesome bolt rocket_launch flag emoji_events military_tech ' +
    'sports_soccer directions_bike directions_car pets cruelty_free flatware kitchen blender ' +
    'child_care stroller toys sports_esports school menu_book auto_stories ' +
    'palette brush format_paint image photo_camera collections wallpaper qr_code barcode_reader ' +
    'shopping_cart_checkout add_shopping_cart production_quantity_limits inventory scale ' +
    'front_hand back_hand waving_hand handshake volunteer_activism favorite_border').split(/\s+/);
  function renderMsGrid(filter) {
    var g = document.getElementById('dzMsGrid'); if (!g) return;
    var list = filter ? MS_ICONS.filter(function (n) { return n.indexOf(filter) > -1; }) : MS_ICONS;
    g.innerHTML = list.slice(0, 60).map(function (n) { return '<button data-drag=\'{"t":"ms","name":"' + n + '"}\' onclick="DZ.addMaterial(\'' + n + '\')" title="' + n + '"><span class="material-icons">' + n + '</span></button>'; }).join('');
  }
  function msSearch() { renderMsGrid(document.getElementById('dzMsQ').value.trim().toLowerCase().replace(/\s+/g, '_')); return false; }
  function rasterMaterial(name, color) {
    return new Promise(function (res) {
      var sp = document.createElement('span'); sp.className = 'material-icons'; sp.textContent = name;
      sp.style.cssText = 'position:fixed;left:-9999px;top:0;font-size:220px;line-height:1;color:' + color + ';';
      document.body.appendChild(sp);
      function go() { if (!window.html2canvas) { document.body.removeChild(sp); return res(''); } html2canvas(sp, { backgroundColor: null, scale: 1 }).then(function (c) { document.body.removeChild(sp); res(c.toDataURL('image/png')); }).catch(function () { document.body.removeChild(sp); res(''); }); }
      if (document.fonts && document.fonts.load) document.fonts.load('220px "Material Icons"').then(go, go); else setTimeout(go, 300);
    });
  }

  // ---- Slepen vanuit het linkerpaneel naar het canvas ------------------------
  function initPanelDrag() {
    panel.addEventListener('pointerdown', function (ev) {
      var it = ev.target.closest('[data-drag]'); if (!it) return;
      var sx = ev.clientX, sy = ev.clientY, dragging = false, ghost = null;
      function mv(m) {
        if (!dragging && Math.hypot(m.clientX - sx, m.clientY - sy) > 6) { dragging = true; ghost = document.createElement('div'); ghost.style.cssText = 'position:fixed;z-index:9999;pointer-events:none;opacity:.85;width:60px;height:60px;background:var(--green-tint);border:2px solid var(--green);border-radius:8px;display:flex;align-items:center;justify-content:center;color:var(--green-d);font-size:1.5rem;box-shadow:0 4px 14px rgba(0,0,0,.2);'; ghost.innerHTML = it.innerHTML; document.body.appendChild(ghost); }
        if (dragging) { ghost.style.left = (m.clientX - 30) + 'px'; ghost.style.top = (m.clientY - 30) + 'px'; }
      }
      function up(m) {
        document.removeEventListener('pointermove', mv); document.removeEventListener('pointerup', up);
        if (ghost) ghost.remove();
        if (dragging) { var r = rect(); if (m.clientX >= r.left && m.clientX <= r.right && m.clientY >= r.top && m.clientY <= r.bottom) { dropAdd(JSON.parse(it.dataset.drag), (m.clientX - r.left) / r.width, (m.clientY - r.top) / r.height); } }
      }
      document.addEventListener('pointermove', mv); document.addEventListener('pointerup', up);
    });
  }
  function dropAdd(d, fx, fy) {
    var pos = { x: fx, y: fy };
    if (d.t === 'text') DZ.addText(d.k, pos);
    else if (d.t === 'shape') DZ.addShape(d.s, pos);
    else if (d.t === 'barcode') DZ.addBarcode(pos);
    else if (d.t === 'table') DZ.addTable(pos);
    else if (d.t === 'icon') DZ.addIcon(d.name, pos);
    else if (d.t === 'ms') DZ.addMaterial(d.name, pos);
    else if (d.t === 'img') DZ.addImage(d.src, d.url, pos);
  }

  // ---- Pagina's --------------------------------------------------------------
  function renderPages() {
    pagesBar.innerHTML = state.pages.map(function (p, i) {
      return '<div class="dz-page ' + (i === state.cur ? 'on' : '') + '" onclick="DZ.goPage(' + i + ')" title="Pagina ' + (i + 1) + '"><span class="pgnum">' + (i + 1) + '</span>' +
        (state.pages.length > 1 ? '<button onclick="event.stopPropagation();DZ.delPage(' + i + ')" style="position:absolute;top:1px;right:1px;border:none;background:none;color:var(--red);cursor:pointer;font-size:.7rem;"><i class="fa fa-xmark"></i></button>' : '') + '</div>';
    }).join('') +
      '<button class="dz-page" onclick="DZ.dupPage()" title="Pagina dupliceren" style="border-style:dashed;"><i class="fa fa-clone"></i></button>' +
      '<button class="dz-addpage" onclick="DZ.addPage()" title="Pagina toevoegen"><i class="fa fa-plus"></i></button>';
  }
  function goPage(i) { state.cur = i; selIds = []; render(); buildProps(); renderPages(); }
  function addPage() { state.pages.push({ bg: '#ffffff', elements: [] }); state.cur = state.pages.length - 1; selIds = []; render(); buildProps(); renderPages(); pushHist(); scheduleSave(); }
  function dupPage() { var c = JSON.parse(JSON.stringify(page())); c.elements.forEach(function (e) { e.id = uid(); }); state.pages.splice(state.cur + 1, 0, c); state.cur++; selIds = []; render(); buildProps(); renderPages(); pushHist(); scheduleSave(); }
  function delPage(i) { if (state.pages.length <= 1) return; if (!confirm('Pagina ' + (i + 1) + ' verwijderen?')) return; state.pages.splice(i, 1); if (state.cur >= state.pages.length) state.cur = state.pages.length - 1; selIds = []; render(); buildProps(); renderPages(); pushHist(); scheduleSave(); }

  // ---- Opslaan / uitvoer -----------------------------------------------------
  function setSave(t) { saveState.textContent = t; }
  function scheduleSave() { setSave('Niet opgeslagen'); clearTimeout(saveTimer); saveTimer = setTimeout(function () { save(false); }, 1400); }
  function save(withThumb) {
    setSave('Opslaan…');
    var body = { title: titleEl.innerText.trim(), data: { w_mm: I.w_mm, h_mm: I.h_mm, pages: state.pages } };
    var fin = function () { fetch(I.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': I.csrf }, credentials: 'same-origin', body: JSON.stringify(body) }).then(function (r) { return r.json(); }).then(function () { setSave('Opgeslagen ✓'); }).catch(function () { setSave('Opslaan mislukt'); }); };
    if (withThumb && window.html2canvas) {
      var was = selIds; selIds = []; render();
      html2canvas(stage, { backgroundColor: page().bg || '#fff', scale: 300 / stage.offsetWidth }).then(function (c) { var t = document.createElement('canvas'); var s = Math.min(1, 420 / c.width); t.width = c.width * s; t.height = c.height * s; t.getContext('2d').drawImage(c, 0, 0, t.width, t.height); body.thumb = t.toDataURL('image/png'); selIds = was; render(); fin(); }).catch(function () { selIds = was; render(); fin(); });
    } else fin();
  }
  function preview() { save(false); window.open(I.previewUrl + '?dpi=150&page=' + state.cur + '&t=' + Date.now(), '_blank'); }
  function printLabel() { var q = prompt('Hoeveel labels printen?', '1'); if (q === null) return; save(false); setTimeout(function () { fetch(I.printLabelUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': I.csrf }, credentials: 'same-origin', body: JSON.stringify({ copies: parseInt(q) || 1 }) }).then(function (r) { return r.json(); }).then(function (d) { alert(d.ok ? 'Naar de labelprinter gestuurd ✓' : ('Printen mislukt: ' + (d.error || '?'))); }).catch(function () { alert('Printen mislukt.'); }); }, 600); }

  // ---- Init ------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    stage = $('#dzStage'); wrap = $('#dzStageWrap'); canvasWrap = $('#dzCanvasWrap'); panel = $('#dzPanel'); props = $('#dzProps'); rail = $('#dzRail'); guides = $('#dzGuides'); titleEl = $('#dzTitle'); saveState = $('#dzSaveState'); zoomLbl = $('#dzZoomLbl'); pagesBar = $('#dzPages');
    setStageBase(); render(); buildProps(); renderPages(); zoomFit();
    $('#dzBgColor').value = page().bg || '#ffffff';
    pushHist(); initPanelDrag(); renderMsGrid('');
    window.addEventListener('resize', function () { applyZoom(); });
    // tab-rail
    rail.querySelectorAll('.dz-railbtn').forEach(function (b) { b.addEventListener('click', function () { rail.querySelectorAll('.dz-railbtn').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on'); panel.querySelectorAll('.dz-pane').forEach(function (p) { p.classList.toggle('on', p.dataset.pane === b.dataset.panel); }); }); });
    // klik op leeg canvas = deselecteren
    wrap.addEventListener('pointerdown', function (ev) { if (ev.target === wrap || ev.target === canvasWrap || ev.target === stage) { selIds = []; render(); buildProps(); } });
    titleEl.addEventListener('blur', scheduleSave);
    $('#dzBgColor').addEventListener('input', function () { DZ.setBg(this.value); });
    $('#dzFile').addEventListener('change', function () { var file = this.files[0]; if (!file) return; var rd = new FileReader(); rd.onload = function (ev) { addToGallery(ev.target.result); DZ.addImage(ev.target.result); }; rd.readAsDataURL(file); this.value = ''; });
    // sneltoetsen
    document.addEventListener('keydown', function (e) {
      if (/^(INPUT|TEXTAREA)$/.test(e.target.tagName) || e.target.isContentEditable) return;
      var m = e.ctrlKey || e.metaKey;
      if (m && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (m && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
      else if (m && e.key.toLowerCase() === 'd') { e.preventDefault(); DZ.dup(); }
      else if (m && e.key.toLowerCase() === 'c') { window._clip = sel() ? JSON.stringify(sel()) : null; }
      else if (m && e.key.toLowerCase() === 'v') { if (window._clip) { var n = JSON.parse(window._clip); n.x += .03; n.y += .03; add(n); } }
      else if (e.key === 'Delete' || e.key === 'Backspace') { if (selIds.length) { e.preventDefault(); DZ.del(); } }
      else if (e.key === 'Escape') { selIds = []; render(); buildProps(); }
      else if (selIds.length && e.key.indexOf('Arrow') === 0) { e.preventDefault(); var dx = (e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0) * (e.shiftKey ? .02 : .004); var dy = (e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0) * (e.shiftKey ? .02 : .004); selIds.forEach(function (id) { var x = el(id); x.x += dx; x.y += dy; }); render(); scheduleSave(); }
    });
    setSave('Opgeslagen ✓');
  });
  function addToGallery(src) { var g = $('#dzGallery'); if (!g) return; var im = document.createElement('img'); im.src = src; im.onclick = function () { DZ.addImage(src); }; g.appendChild(im); }
})();
