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
    return a ? String(a.getAttribute('data-zen-id')).slice(0, 300) : '';
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
  window.addEventListener('message', function (e) {
    var d = e.data;
    if (e.source !== window.parent || !d || typeof d !== 'object') return;
    if (d.zen === 'mode') setMode(d.pick === true);
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
