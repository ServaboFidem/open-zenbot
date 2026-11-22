(function () {
  'use strict';
  var pickOn = false, box = null, layer = null, pins = [];
  function send(msg) { try { parent.postMessage(msg, '*'); } catch (e) {} }
  function root() { return document.body || document.documentElement; }
  function ensureBox() {
    if (box && box.isConnected) return box;
    box = document.createElement('div');
    box.setAttribute('data-zen-ui', '');
    box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;outline:2px solid #0f6cbd;background:rgba(15,108,189,.12);display:none';
    root().appendChild(box);
    return box;
  }
  function mine(el) { return !!(el && el.closest && el.closest('[data-zen-ui]')); }
  function pathOf(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.body && el !== document.documentElement) {
      var n = 1, sib = el;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === el.tagName) n++;
      parts.unshift(el.tagName.toLowerCase() + ':nth-of-type(' + n + ')');
      el = el.parentElement;
    }
    return ('body > ' + parts.join(' > ')).slice(0, 300);
  }
  function idOf(el) {
    var a = el.closest ? el.closest('[data-zen-id]') : null;
    return a ? String(a.getAttribute('data-zen-id')).slice(0, 120) : '';
  }
  function target(e) { return e.target && e.target.nodeType === 1 ? e.target : null; }
  function hover(e) {
    var el = target(e);
    if (!pickOn || !el || mine(el)) return;
    var r = el.getBoundingClientRect(), b = ensureBox();
    b.style.display = 'block';
    b.style.left = r.left + 'px'; b.style.top = r.top + 'px';
    b.style.width = r.width + 'px'; b.style.height = r.height + 'px';
  }
  function click(e) {
    var el = target(e);
    if (!el || mine(el)) return;
    if (pickOn) {
      e.preventDefault(); e.stopPropagation();
      var r = el.getBoundingClientRect();
      send({
        zen: 'pick', elementId: idOf(el), selector: pathOf(el), tag: el.tagName.toLowerCase(),
        text: String(el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160),
        rect: { x: r.left, y: r.top, w: r.width, h: r.height }
      });
      return;
    }
    var a = el.closest ? el.closest('a[href]') : null;
    var href = a ? String(a.getAttribute('href')) : '';
    if (a && href.charAt(0) !== '#') { e.preventDefault(); e.stopPropagation(); }
  }
  function setMode(on) {
    pickOn = on;
    if (!on && box) box.style.display = 'none';
    if (document.documentElement) document.documentElement.style.cursor = on ? 'crosshair' : '';
  }
  function find(pin) {
    try {
      if (pin.elementId) {
        var all = document.querySelectorAll('[data-zen-id]');
        for (var i = 0; i < all.length; i++) if (all[i].getAttribute('data-zen-id') === pin.elementId) return all[i];
      }
      return pin.selector ? document.querySelector(pin.selector) : null;
    } catch (e) { return null; }
  }
  function draw() {
    if (layer) layer.remove();
    layer = document.createElement('div');
    layer.setAttribute('data-zen-ui', '');
    layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;pointer-events:none;z-index:2147483646';
    pins.forEach(function (p) {
      var el = find(p);
      if (!el) return;
      var r = el.getBoundingClientRect();
      var m = document.createElement('div');
      m.textContent = String(p.n);
      m.style.cssText = 'position:absolute;min-width:18px;height:18px;border-radius:9px;background:#0f6cbd;color:#fff;font:600 11px/18px sans-serif;text-align:center;padding:0 4px;box-sizing:border-box;left:' +
        (r.left + window.pageXOffset) + 'px;top:' + (r.top + window.pageYOffset) + 'px';
      layer.appendChild(m);
    });
    root().appendChild(layer);
  }
  // Unsaved changes the GUI asks the frame to show. Display only: nothing here is sent back.
  // varsBefore: each custom property the bridge set on the root, as the page had it before the first write
  var touched = [], varsBefore = [];
  var IMAGE_SRC = /^data:image\/(png|jpeg|webp|gif);base64,/;
  var VAR_NAME = /^--[a-z][a-z0-9-]{0,40}$/;
  function byId(id) {
    var all = document.querySelectorAll('[data-zen-id]');
    for (var i = 0; i < all.length; i++) if (all[i].getAttribute('data-zen-id') === id) return all[i];
    return null;
  }
  function remember(el) {
    for (var i = 0; i < touched.length; i++) if (touched[i].el === el) return touched[i];
    var t = { el: el, kids: Array.prototype.slice.call(el.childNodes), value: el.value,
      hadSrc: el.hasAttribute('src'), src: el.getAttribute('src'), hadAlt: el.hasAttribute('alt'), alt: el.getAttribute('alt') };
    touched.push(t);
    return t;
  }
  function clearKids(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  // No text goes into a style, a script, a picture or an SVG drawing (or anything inside one), whatever its id says.
  var NO_TEXT = /^(STYLE|SCRIPT|IMG|SVG)$/;
  function takesText(el) {
    if (NO_TEXT.test(String(el.tagName).toUpperCase())) return false;
    for (var n = el; n && n.nodeType === 1; n = n.parentNode)
      if (n.namespaceURI === 'http://www.w3.org/2000/svg' || String(n.tagName).toUpperCase() === 'SVG') return false;
    return true;
  }
  function showText(id, text) {
    var el = byId(id);
    if (!el || !takesText(el)) return;
    remember(el);
    if (el.tagName === 'TEXTAREA') { el.value = text; return; }
    clearKids(el);
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
      if (i) el.appendChild(document.createElement('br'));
      if (lines[i]) el.appendChild(document.createTextNode(lines[i]));
    }
  }
  function showImage(id, src, alt) {
    var el = byId(id);
    if (!el || el.tagName !== 'IMG') return;
    remember(el);
    el.setAttribute('src', src);
    el.setAttribute('alt', alt);
  }
  function showVars(vars) {
    var names = Object.keys(vars);
    if (names.length > 8) return;
    for (var i = 0; i < names.length; i++) {
      var v = vars[names[i]];
      if (!VAR_NAME.test(names[i]) || typeof v !== 'string' || v.length > 32) return;
    }
    var style = document.documentElement.style;
    for (var j = 0; j < names.length; j++) {
      var n = names[j], seen = false;
      for (var k = 0; k < varsBefore.length; k++) if (varsBefore[k].name === n) seen = true;
      if (!seen) {  // a later write keeps the first remembered state
        var was = style.getPropertyValue(n);
        varsBefore.push({ name: n, had: was !== '', value: was, priority: style.getPropertyPriority(n) });
      }
      style.setProperty(n, vars[n]);
    }
  }
  function resetAll() {
    touched.forEach(function (t) {
      var el = t.el;
      if (el.tagName === 'TEXTAREA') el.value = t.value;
      else { clearKids(el); t.kids.forEach(function (k) { el.appendChild(k); }); }
      if (el.tagName === 'IMG') {
        if (t.hadSrc) el.setAttribute('src', t.src); else el.removeAttribute('src');
        if (t.hadAlt) el.setAttribute('alt', t.alt); else el.removeAttribute('alt');
      }
    });
    touched = [];
    // the page's own inline value comes back; a property it did not have goes
    var style = document.documentElement.style;
    varsBefore.forEach(function (v) {
      if (v.had) style.setProperty(v.name, v.value, v.priority); else style.removeProperty(v.name);
    });
    varsBefore = [];
  }
  window.addEventListener('message', function (e) {
    var d = e.data;
    if (e.source !== window.parent || !d || typeof d !== 'object') return;
    if (d.zen === 'mode') setMode(d.pick === true);
    else if (d.zen === 'text') {
      if (typeof d.elementId === 'string' && typeof d.text === 'string' && d.text.length <= 2000) { showText(d.elementId, d.text); draw(); }
    } else if (d.zen === 'image') {
      if (typeof d.elementId === 'string' && typeof d.src === 'string' && typeof d.alt === 'string' && IMAGE_SRC.test(d.src)) { showImage(d.elementId, d.src, d.alt); draw(); }
    } else if (d.zen === 'vars') {
      if (d.vars && typeof d.vars === 'object' && !Array.isArray(d.vars)) { showVars(d.vars); draw(); }
    } else if (d.zen === 'reset') { resetAll(); draw(); }
    else if (d.zen === 'pins' && Array.isArray(d.pins)) {
      pins = d.pins.filter(function (p) { return p && typeof p === 'object'; })
        .map(function (p) { return { n: p.n, elementId: typeof p.elementId === 'string' ? p.elementId : '', selector: typeof p.selector === 'string' ? p.selector : '' }; });
      draw();
    }
  });
  document.addEventListener('mouseover', hover, true);
  document.addEventListener('click', click, true);
  window.addEventListener('resize', function () { if (pins.length) draw(); });
  send({ zen: 'ready' });
})();
