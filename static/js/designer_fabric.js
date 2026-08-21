/* PLUSLokaal Designer - Fabric.js-editor (v1).
   Eén renderer (Fabric in de browser): het canvas is de bron voor scherm EN print.
   - Opslaan: Fabric-JSON per pagina + thumbnail.
   - Voorbeeld/PDF/printen: client exporteert PNG op de juiste DPI; server pakt het alleen in. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var I = window.DZ_INIT;
  var MM = 25.4, BASE_DPI = 96;                 // canvas-basis: 96 dpi (px = mm/25.4*96)
  var Wpx = Math.round(I.w_mm / MM * BASE_DPI);
  var Hpx = Math.round(I.h_mm / MM * BASE_DPI);

  var fc, wrap, zoom = 1, pages = [], cur = 0, hist = [], hi = -1, muteHist = false;
  var titleEl, saveState, zoomLbl, pagesBar;

  // ---------- init ----------
  document.addEventListener('DOMContentLoaded', function () {
    titleEl = $('#dzTitle'); saveState = $('#dzSaveState'); zoomLbl = $('#dzZoomLbl');
    pagesBar = $('#dzPages'); wrap = $('#dzStageWrap');

    fabric.Object.prototype.set({
      cornerColor: '#fff', cornerStrokeColor: '#4a9b28', borderColor: '#4a9b28',
      cornerStyle: 'circle', cornerSize: 11, transparentCorners: false, padding: 2
    });
    var cnv = document.createElement('canvas');
    cnv.id = 'dzCanvas'; cnv.width = Wpx; cnv.height = Hpx;
    $('#dzStage').appendChild(cnv);
    fc = new fabric.Canvas('dzCanvas', {
      width: Wpx, height: Hpx, backgroundColor: '#ffffff', preserveObjectStacking: true
    });

    // pagina's laden (nieuw: pages[fabricJSON]; oud/leeg: 1 lege pagina)
    var d = I.data || {};
    if (d.pages && d.pages.length && d.pages[0] && d.pages[0].objects !== undefined) {
      pages = d.pages;
    } else if (d.fabric) { pages = [d.fabric]; }
    else { pages = [{ background: '#ffffff', objects: [] }]; }
    loadPage(0, function () { pushHist(); });

    bindUI();
    zoomFit();
    window.addEventListener('resize', zoomFit);

    fc.on('selection:created', buildProps);
    fc.on('selection:updated', buildProps);
    fc.on('selection:cleared', buildProps);
    fc.on('object:modified', function () { commit(); });
    renderPagesBar();
  });

  // ---------- pagina's ----------
  function snapshotCurrent() {
    pages[cur] = fc.toJSON(['selectable', 'dzType', 'dzMeta']);
    pages[cur].background = fc.backgroundColor;
  }
  function loadPage(idx, done) {
    cur = idx;
    var pj = pages[idx] || { objects: [] };
    muteHist = true;
    fc.loadFromJSON(pj, function () {
      fc.backgroundColor = pj.background || '#ffffff';
      fc.renderAll(); muteHist = false; buildProps();
      $('#dzBgColor') && ($('#dzBgColor').value = toHex(fc.backgroundColor));
      if (done) done();
    });
  }
  function goPage(idx) { if (idx === cur) return; snapshotCurrent(); loadPage(idx, function () { renderPagesBar(); }); }
  function addPage() { snapshotCurrent(); pages.push({ background: '#ffffff', objects: [] }); goPage(pages.length - 1); commit(); }
  function dupPage() { snapshotCurrent(); pages.splice(cur + 1, 0, JSON.parse(JSON.stringify(pages[cur]))); goPage(cur + 1); commit(); }
  function delPage() { if (pages.length <= 1) return; snapshotCurrent(); pages.splice(cur, 1); goPage(Math.max(0, cur - 1)); commit(); }
  function renderPagesBar() {
    if (!pagesBar) return;
    pagesBar.innerHTML = '';
    pages.forEach(function (p, i) {
      var b = document.createElement('button'); b.className = 'dz-page' + (i === cur ? ' on' : '');
      b.innerHTML = '<span class="pgnum">' + (i + 1) + '</span>';
      b.onclick = function () { goPage(i); };
      pagesBar.appendChild(b);
    });
    var add = document.createElement('button'); add.className = 'dz-addpage'; add.textContent = '+';
    add.title = 'Pagina toevoegen'; add.onclick = addPage; pagesBar.appendChild(add);
  }

  // ---------- history ----------
  function commit() { if (muteHist) return; snapshotCurrent(); pushHist(); scheduleSave(); }
  function pushHist() {
    snapshotCurrent();
    var s = JSON.stringify({ pages: pages, cur: cur });
    hist = hist.slice(0, hi + 1); hist.push(s); if (hist.length > 50) hist.shift();
    hi = hist.length - 1; updUndo();
  }
  function updUndo() { $('#dzUndo').disabled = hi <= 0; $('#dzRedo').disabled = hi >= hist.length - 1; }
  function restore(s) {
    var o = JSON.parse(s); pages = o.pages; cur = Math.min(o.cur || 0, pages.length - 1);
    loadPage(cur, function () { renderPagesBar(); }); scheduleSave();
  }
  function undo() { if (hi > 0) { hi--; restore(hist[hi]); updUndo(); } }
  function redo() { if (hi < hist.length - 1) { hi++; restore(hist[hi]); updUndo(); } }

  // ---------- add-elementen ----------
  var TXT = { kop: { fontSize: 0.09, fontWeight: 700 }, sub: { fontSize: 0.055, fontWeight: 700 }, body: { fontSize: 0.032, fontWeight: 400 } };
  function place(obj) {
    obj.set({ left: Wpx * 0.5, top: Hpx * 0.4, originX: 'center', originY: 'center' });
    fc.add(obj); fc.setActiveObject(obj); fc.requestRenderAll(); commit();
  }
  function addText(kind) {
    var cfg = TXT[kind] || TXT.body;
    var t = new fabric.Textbox(kind === 'kop' ? 'Kop' : kind === 'sub' ? 'Subkop' : 'Bodytekst', {
      width: Wpx * 0.7, fontSize: Math.round(cfg.fontSize * Hpx), fontWeight: cfg.fontWeight,
      fontFamily: 'Montserrat', fill: '#231f20', textAlign: 'left'
    });
    place(t);
  }
  function addShape(s) {
    var col = '#80bd1d', w = Wpx * 0.3, h = Hpx * 0.2, o;
    if (s === 'rect') o = new fabric.Rect({ width: w, height: h, fill: col, rx: 0, ry: 0 });
    else if (s === 'ellipse') o = new fabric.Ellipse({ rx: w / 2, ry: h / 2, fill: col });
    else if (s === 'triangle') o = new fabric.Triangle({ width: w, height: h, fill: col });
    else if (s === 'line') o = new fabric.Line([0, 0, w, 0], { stroke: '#231f20', strokeWidth: Math.max(2, Hpx * 0.006) });
    else if (s === 'star') o = starPoly(col);
    else if (s === 'arrow') o = arrowGroup(col, w, h);
    if (o) place(o);
  }
  function starPoly(col) {
    var pts = [], ro = 60, ri = 25;
    for (var i = 0; i < 10; i++) { var r = i % 2 ? ri : ro, a = Math.PI / 2 + i * Math.PI / 5; pts.push({ x: r * Math.cos(a), y: -r * Math.sin(a) }); }
    return new fabric.Polygon(pts, { fill: col });
  }
  function arrowGroup(col, w, h) {
    var th = Math.max(3, h * 0.18), line = new fabric.Rect({ left: 0, top: h / 2 - th / 2, width: w * 0.7, height: th, fill: col });
    var head = new fabric.Triangle({ left: w * 0.62, top: 0, width: h, height: h, fill: col, angle: 90 });
    return new fabric.Group([line, head]);
  }
  function addImage(dataUrl, meta) {
    fabric.Image.fromURL(dataUrl, function (img) {
      var sc = Math.min(Wpx * 0.5 / img.width, Hpx * 0.5 / img.height, 1);
      img.scale(sc); if (meta) img.dzMeta = meta; place(img);
    }, { crossOrigin: 'anonymous' });
  }
  function addIcon(name) {
    rasterIcon(name, '#115013').then(function (url) { addImage(url, { icon: name }); });
  }
  function rasterIcon(name, color) {
    return new Promise(function (res) {
      var i = document.createElement('i'); i.className = 'fa fa-' + name;
      i.style.cssText = 'position:absolute;left:-9999px;font-weight:900;font-size:200px;color:' + color;
      document.body.appendChild(i);
      var cs = getComputedStyle(i, '::before'), ch = cs.content.replace(/["']/g, '');
      var fam = cs.fontFamily, fw = cs.fontWeight; document.body.removeChild(i);
      var c = document.createElement('canvas'); c.width = c.height = 256; var x = c.getContext('2d');
      x.fillStyle = color; x.font = fw + ' 200px ' + fam; x.textAlign = 'center'; x.textBaseline = 'middle';
      x.fillText(ch, 128, 138); res(c.toDataURL('image/png'));
    });
  }
  function addBarcode() {
    var v = prompt('Barcode (EAN13/EAN8 of tekst):', '8710400145829'); if (!v) return;
    var url = I.barcodeUrl + '?value=' + encodeURIComponent(v) + '&showtext=1';
    fabric.Image.fromURL(url, function (img) {
      img.scaleToWidth(Wpx * 0.5); img.dzMeta = { barcode: v }; place(img);
    }, { crossOrigin: 'anonymous' });
  }
  function addTable() { addText('body'); } // eenvoudige placeholder; volwaardige tabel volgt

  // ---------- background ----------
  function setBg(col) { fc.backgroundColor = col; fc.requestRenderAll(); $('#dzBgColor') && ($('#dzBgColor').value = toHex(col)); commit(); }

  // ---------- props-paneel ----------
  function buildProps() {
    var p = $('#dzProps'); if (!p) return;
    var o = fc.getActiveObject();
    if (!o) { p.innerHTML = '<div class="dz-empty">Selecteer een element om het te bewerken.</div>'; return; }
    var isText = o.type === 'textbox' || o.type === 'i-text' || o.type === 'text';
    var html = '<h4>Element</h4>';
    if (isText) {
      html += row('Tekstkleur', colorInput('fill', o.fill || '#231f20'));
      html += row('Tekengrootte', rangeInput('fontSize', 8, Math.round(Hpx * 0.25), o.fontSize, 1));
      html += row('Lettertype', selectInput('fontFamily', o.fontFamily, [['Montserrat', 'Montserrat'], ['Gothic A1', 'GothicA1']]));
      html += '<div class="dz-btnrow"><button class="dz-mini' + (o.fontWeight >= 700 ? ' on' : '') + '" data-toggle="fontWeight">Vet</button>'
        + '<button class="dz-mini' + (o.fontStyle === 'italic' ? ' on' : '') + '" data-toggle="fontStyle">Cursief</button></div>'
        + '<div class="dz-btnrow" style="margin-top:6px;"><button class="dz-mini" data-align="left">Links</button><button class="dz-mini" data-align="center">Midden</button><button class="dz-mini" data-align="right">Rechts</button></div>';
    } else if (o.type === 'image') {
      html += '<p class="hint" style="margin:0 0 8px;">Sleep de hoeken om te schalen.</p>';
    } else {
      if (o.fill !== undefined) html += row('Vulkleur', colorInput('fill', o.fill || '#80bd1d'));
      if (o.stroke !== undefined || o.type !== 'group') html += row('Lijnkleur', colorInput('stroke', o.stroke || '#231f20'));
      if (o.rx !== undefined) html += row('Ronding', rangeInput('rx', 0, Math.round(Math.min(o.width, o.height) / 2), o.rx || 0, 1));
    }
    html += '<h4>Algemeen</h4>';
    html += row('Transparantie', rangeInput('opacity', 0, 100, Math.round((o.opacity != null ? o.opacity : 1) * 100), 1));
    html += '<div class="dz-btnrow"><button class="dz-mini" data-cmd="front">Naar voren</button><button class="dz-mini" data-cmd="back">Naar achter</button></div>';
    html += '<div class="dz-btnrow" style="margin-top:6px;"><button class="dz-mini" data-cmd="dup">Dupliceren</button><button class="dz-mini" data-cmd="del" style="color:#dd350d;">Verwijderen</button></div>';
    p.innerHTML = html;
    bindProps(p, o);
  }
  function row(lbl, inner) { return '<div class="dz-f"><label>' + lbl + '</label>' + inner + '</div>'; }
  function colorInput(k, v) { return '<input type="color" data-prop="' + k + '" value="' + toHex(v) + '">'; }
  function rangeInput(k, mn, mx, v, st) { return '<input type="range" data-prop="' + k + '" min="' + mn + '" max="' + mx + '" step="' + st + '" value="' + v + '">'; }
  function selectInput(k, v, opts) { return '<select data-prop="' + k + '">' + opts.map(function (o) { return '<option value="' + o[1] + '"' + (o[1] === v ? ' selected' : '') + '>' + o[0] + '</option>'; }).join('') + '</select>'; }
  function bindProps(p, o) {
    p.querySelectorAll('[data-prop]').forEach(function (el) {
      el.addEventListener('input', function () {
        var k = el.dataset.prop, val = el.value;
        if (k === 'opacity') val = (parseFloat(val) / 100);
        else if (k === 'fontSize' || k === 'rx' || k === 'ry') val = parseFloat(val);
        if (k === 'rx') { o.set('rx', val); o.set('ry', val); } else o.set(k, val);
        fc.requestRenderAll();
      });
      el.addEventListener('change', commit);
    });
    p.querySelectorAll('[data-toggle]').forEach(function (b) {
      b.onclick = function () { var k = b.dataset.toggle; if (k === 'fontWeight') o.set('fontWeight', o.fontWeight >= 700 ? 400 : 700); else o.set('fontStyle', o.fontStyle === 'italic' ? 'normal' : 'italic'); fc.requestRenderAll(); buildProps(); commit(); };
    });
    p.querySelectorAll('[data-align]').forEach(function (b) { b.onclick = function () { o.set('textAlign', b.dataset.align); fc.requestRenderAll(); commit(); }; });
    p.querySelectorAll('[data-cmd]').forEach(function (b) {
      b.onclick = function () {
        var c = b.dataset.cmd;
        if (c === 'front') o.bringToFront(); else if (c === 'back') o.sendToBack();
        else if (c === 'del') { fc.remove(o); }
        else if (c === 'dup') { o.clone(function (cl) { cl.set({ left: o.left + 16, top: o.top + 16 }); fc.add(cl); fc.setActiveObject(cl); }); }
        fc.requestRenderAll(); commit(); buildProps();
      };
    });
  }

  // ---------- zoom ----------
  function applyZoom() {
    var cw = $('#dzCanvasWrap');
    cw.style.transform = 'scale(' + zoom + ')';
    zoomLbl.textContent = Math.round(zoom * 100) + '%';
  }
  function zoomBy(d) { zoom = Math.min(4, Math.max(0.1, zoom + d)); applyZoom(); }
  function zoomFit() {
    var availW = wrap.clientWidth - 48, availH = wrap.clientHeight - 48;
    zoom = Math.max(0.1, Math.min(availW / Wpx, availH / Hpx, 2)); applyZoom();
  }

  // ---------- opslaan / export ----------
  var saveT;
  function scheduleSave() { setSave('Wijzigingen…'); clearTimeout(saveT); saveT = setTimeout(function () { save(false); }, 1200); }
  function setSave(s) { if (saveState) saveState.textContent = s; }
  function exportPagePng(idx, dpi) {
    // exporteer pagina idx op gegeven dpi als PNG-dataURL (client-render = print-render)
    var mult = dpi / BASE_DPI;
    if (idx === cur) return Promise.resolve(fc.toDataURL({ format: 'png', multiplier: mult }));
    // andere pagina: tijdelijk in een offscreen canvas laden
    return new Promise(function (res) {
      var tmp = new fabric.StaticCanvas(null, { width: Wpx, height: Hpx });
      tmp.loadFromJSON(pages[idx], function () {
        tmp.backgroundColor = (pages[idx] && pages[idx].background) || '#ffffff';
        tmp.renderAll();
        res(tmp.toDataURL({ format: 'png', multiplier: mult })); tmp.dispose();
      });
    });
  }
  function save(withThumb) {
    setSave('Opslaan…'); snapshotCurrent();
    var body = { title: titleEl.innerText.trim(), data: { w_mm: I.w_mm, h_mm: I.h_mm, pages: pages } };
    function fin() {
      return fetch(I.saveUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': I.csrf }, credentials: 'same-origin', body: JSON.stringify(body) })
        .then(function (r) { return r.json(); }).then(function () { setSave('Opgeslagen'); }).catch(function () { setSave('Opslaan mislukt'); });
    }
    if (withThumb) { body.thumb = fc.toDataURL({ format: 'png', multiplier: Math.min(1, 420 / Wpx) }); }
    return fin();
  }
  function preview() {
    var m = $('#dzPreviewModal'), img = $('#dzPreviewImg');
    m._pg = cur; $('#dzPreviewPg').textContent = 'Voorbeeld…'; m.classList.add('open');
    save(false).then(function () { showPreviewPage(cur); });
  }
  function showPreviewPage(pg) {
    var np = pages.length; if (pg < 0) pg = 0; if (pg >= np) pg = np - 1;
    var m = $('#dzPreviewModal'); m._pg = pg; var img = $('#dzPreviewImg'); img.classList.add('loading');
    exportPagePng(pg, 200).then(function (url) { img.onload = function () { img.classList.remove('loading'); }; img.src = url; });
    $('#dzPreviewPg').textContent = np > 1 ? ('Pagina ' + (pg + 1) + ' / ' + np) : '';
    $('#dzPrevPg').style.display = $('#dzNextPg').style.display = (np > 1 ? '' : 'none');
    $('#dzPrevPg').disabled = pg <= 0; $('#dzNextPg').disabled = pg >= np - 1;
  }
  function closePreview() { $('#dzPreviewModal').classList.remove('open'); }
  function downloadPDF() {
    setSave('PDF maken…');
    var jobs = pages.map(function (_, i) { return exportPagePng(i, 300); });
    Promise.all(jobs).then(function (pngs) {
      return fetch(I.pdfUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': I.csrf }, credentials: 'same-origin', body: JSON.stringify({ pages: pngs }) })
        .then(function (r) { return r.blob(); }).then(function (b) {
          var a = document.createElement('a'); a.href = URL.createObjectURL(b);
          a.download = (titleEl.innerText.trim() || 'ontwerp') + '.pdf'; document.body.appendChild(a); a.click();
          setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500); setSave('Opgeslagen');
        });
    }).catch(function () { setSave('PDF mislukt'); });
  }
  function printLabel() {
    var q = prompt('Hoeveel labels printen?', '1'); if (q === null) return;
    exportPagePng(cur, 300).then(function (png) {
      return fetch(I.printLabelUrl, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': I.csrf }, credentials: 'same-origin', body: JSON.stringify({ png: png, copies: parseInt(q) || 1 }) })
        .then(function (r) { return r.json(); }).then(function (d) { alert(d.ok ? 'Naar de labelprinter gestuurd' : ('Printen mislukt: ' + (d.error || '?'))); });
    }).catch(function () { alert('Printen mislukt.'); });
  }

  // ---------- PLUS-zoek ----------
  function plusSearch() {
    var q = $('#dzPlusQ').value.trim(); if (q.length < 2) return false;
    var res = $('#dzPlusRes'); res.innerHTML = '<p class="hint">Zoeken op plus.nl…</p>';
    fetch(I.plusUrl + '?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (d) {
      if (!Array.isArray(d) || !d.length) { res.innerHTML = '<p class="hint">Geen resultaten.</p>'; return; }
      res.innerHTML = d.map(function (x, i) {
        return '<div class="dz-pr"><div class="dz-pr__top">' + (x.img ? '<img class="dz-pr__img" src="' + x.img + '">' : '') +
          '<div><div class="dz-pr__name">' + esc(x.naam) + '</div><div class="dz-pr__price">' + (x.prijs != null ? ('€ ' + x.prijs) : (x.actie != null ? ('€ ' + x.actie) : '')) + '</div></div></div>' +
          '<button class="btn btn-primary btn-sm btn-block" data-plus="' + i + '">Toevoegen</button></div>';
      }).join('');
      res.querySelectorAll('[data-plus]').forEach(function (b) {
        b.onclick = function () {
          var x = d[+b.dataset.plus];
          if (x.img) addImage(x.img.split('?')[0] + '?w=800&fm=png', { plus: x.naam });
          if (x.naam) { var t = new fabric.Textbox(x.naam, { width: Wpx * 0.6, fontSize: Math.round(0.05 * Hpx), fontFamily: 'Montserrat', fontWeight: 700, fill: '#231f20' }); place(t); }
        };
      });
    }).catch(function () { res.innerHTML = '<p class="hint">Zoeken mislukt.</p>'; });
    return false;
  }

  // ---------- UI-binding ----------
  function bindUI() {
    var rail = $('#dzRail'), panel = $('#dzPanel');
    rail.querySelectorAll('.dz-railbtn').forEach(function (b) {
      b.addEventListener('click', function () {
        rail.querySelectorAll('.dz-railbtn').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on');
        panel.querySelectorAll('.dz-pane').forEach(function (p) { p.classList.toggle('on', p.dataset.pane === b.dataset.panel); });
      });
    });
    titleEl.addEventListener('blur', function () { save(false); });
    $('#dzBgColor') && $('#dzBgColor').addEventListener('input', function () { setBg(this.value); });
    $('#dzFile') && $('#dzFile').addEventListener('change', function () { var f = this.files[0]; if (!f) return; var rd = new FileReader(); rd.onload = function (e) { addImage(e.target.result); }; rd.readAsDataURL(f); this.value = ''; });
    // voorbeeld-modal
    $('#dzPreviewClose').addEventListener('click', closePreview);
    $('#dzPreviewModal').addEventListener('click', function (e) { if (e.target === this) closePreview(); });
    $('#dzPrevPg').addEventListener('click', function () { showPreviewPage(($('#dzPreviewModal')._pg || 0) - 1); });
    $('#dzNextPg').addEventListener('click', function () { showPreviewPage(($('#dzPreviewModal')._pg || 0) + 1); });
    // sneltoetsen
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closePreview();
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
      if ((e.key === 'Delete' || e.key === 'Backspace') && fc.getActiveObject() && !/INPUT|TEXTAREA/.test(document.activeElement.tagName) && !fc.getActiveObject().isEditing) { fc.remove(fc.getActiveObject()); fc.requestRenderAll(); commit(); }
    });
  }

  // ---------- helpers ----------
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function toHex(c) {
    if (!c) return '#ffffff'; if (c[0] === '#') return c.slice(0, 7);
    var m = String(c).match(/\d+/g); if (!m) return '#ffffff';
    return '#' + m.slice(0, 3).map(function (n) { return ('0' + (+n).toString(16)).slice(-2); }).join('');
  }

  // publieke API voor de knoppen in de template
  window.DZ = {
    addText: addText, addShape: addShape, addIcon: addIcon, addBarcode: addBarcode, addTable: addTable,
    addImage: addImage, setBg: setBg, undo: undo, redo: redo, zoomBy: zoomBy, zoomFit: zoomFit,
    save: save, preview: preview, printLabel: printLabel, downloadPDF: downloadPDF,
    plusSearch: plusSearch, addPage: addPage, dupPage: dupPage, delPage: delPage,
    msSearch: function () { return false; }
  };
})();
