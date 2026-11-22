// The preview bridge shows the user's unsaved text, pictures and tweaks, and sends nothing back.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../src/harness/design_bridge.js', import.meta.url), 'utf8');

class Node_ {
  constructor(tag, text) { this.tagName = tag; this.nodeType = tag === '#text' ? 3 : 1; this.text = text; this.childNodes = []; this.attrs = {}; this.value = ''; this.parentNode = null; this.style = {}; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  removeChild(n) { this.childNodes.splice(this.childNodes.indexOf(n), 1); n.parentNode = null; return n; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  all() { return this.childNodes.flatMap((c) => [c, ...c.all()]); }
  shape() { return this.childNodes.map((c) => (c.tagName === '#text' ? c.text : '<' + c.tagName + '>')); }
}

function setup() {
  const props = {};
  const root = new Node_('HTML');
  root.style = { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } };
  const body = new Node_('BODY');
  root.appendChild(body);
  const p = new Node_('P'); p.attrs['data-zen-id'] = 'p1'; body.appendChild(p);
  p.appendChild(new Node_('#text', 'Original ')); p.appendChild(new Node_('B')); p.appendChild(new Node_('#text', ' end'));
  const img = new Node_('IMG'); img.attrs['data-zen-id'] = 'i1'; img.attrs.src = 'images/a.png'; img.attrs.alt = 'old'; body.appendChild(img);
  const ta = new Node_('TEXTAREA'); ta.attrs['data-zen-id'] = 't1'; ta.value = 'was'; body.appendChild(ta);
  const sent = []; const handlers = {};
  const parent = { postMessage: (m) => sent.push(m) };
  const document = {
    body, documentElement: root,
    createElement: (t) => new Node_(t.toUpperCase()), createTextNode: (t) => new Node_('#text', t),
    querySelectorAll: () => root.all().filter((n) => n.hasAttribute && n.hasAttribute('data-zen-id')),
    querySelector: () => null, addEventListener: () => {},
  };
  const window = { parent, addEventListener: (t, f) => { handlers[t] = f; }, pageXOffset: 0, pageYOffset: 0 };
  vm.runInNewContext(SRC, { window, document, parent });
  const post = (data, source = parent) => handlers.message({ data, source });
  return { p, img, ta, props, sent, post };
}

test('bridge: text becomes text nodes and br, never markup', () => {
  const { p, post } = setup();
  post({ zen: 'text', elementId: 'p1', text: '<b>x</b>\ny' });
  assert.deepEqual(p.shape(), ['<b>x</b>', '<BR>', 'y']);
  assert.equal(p.childNodes.filter((c) => c.tagName === 'B').length, 0);
});

test('bridge: a textarea takes the text as its value; an unknown id and long text do nothing', () => {
  const { p, ta, post } = setup();
  post({ zen: 'text', elementId: 't1', text: 'a\nb' });
  assert.equal(ta.value, 'a\nb');
  post({ zen: 'text', elementId: 'nope', text: 'x' });
  post({ zen: 'text', elementId: 'p1', text: 'x'.repeat(2001) });
  assert.equal(p.shape().length, 3);
});

test('bridge: image accepts only the four raster data types on an img', () => {
  const { p, img, post } = setup();
  for (const src of ['http://x/a.png', 'data:image/svg+xml;base64,AAAA', 'data:image/png,AAAA'])
    post({ zen: 'image', elementId: 'i1', src, alt: 'n' });
  assert.equal(img.getAttribute('src'), 'images/a.png');
  post({ zen: 'image', elementId: 'p1', src: 'data:image/png;base64,AAAA', alt: 'n' });
  assert.equal(p.getAttribute('src'), null);
  post({ zen: 'image', elementId: 'i1', src: 'data:image/webp;base64,AAAA', alt: 'new' });
  assert.equal(img.getAttribute('src'), 'data:image/webp;base64,AAAA');
  assert.equal(img.getAttribute('alt'), 'new');
});

test('bridge: vars take only --names, at most 8, values up to 32 characters', () => {
  const { props, post } = setup();
  post({ zen: 'vars', vars: { color: 'red' } });
  post({ zen: 'vars', vars: { '--a': 'x'.repeat(33) } });
  const nine = {};
  for (let i = 0; i < 9; i++) nine['--v' + i] = '1';
  post({ zen: 'vars', vars: nine });
  assert.deepEqual(props, {});
  post({ zen: 'vars', vars: { '--accent': '#ff0000' } });
  assert.deepEqual(props, { '--accent': '#ff0000' });
});

test('bridge: reset restores the first state after two edits and removes the properties', () => {
  const { p, img, ta, props, post } = setup();
  const kids = p.childNodes.slice();
  post({ zen: 'text', elementId: 'p1', text: 'one' });
  post({ zen: 'text', elementId: 'p1', text: 'two\nthree' });
  post({ zen: 'image', elementId: 'i1', src: 'data:image/png;base64,AA', alt: 'a' });
  post({ zen: 'image', elementId: 'i1', src: 'data:image/gif;base64,AA', alt: 'b' });
  post({ zen: 'text', elementId: 't1', text: 'z' });
  post({ zen: 'vars', vars: { '--accent': '#fff' } });
  post({ zen: 'reset' });
  assert.deepEqual(p.childNodes, kids);
  assert.equal(img.getAttribute('src'), 'images/a.png');
  assert.equal(img.getAttribute('alt'), 'old');
  assert.equal(ta.value, 'was');
  assert.deepEqual(props, {});
});

test('bridge: a message from another source is ignored, and the frame sends only ready', () => {
  const { p, props, sent, post } = setup();
  post({ zen: 'text', elementId: 'p1', text: 'x' }, {});
  post({ zen: 'vars', vars: { '--a': '1' } }, null);
  assert.equal(p.shape().length, 3);
  assert.deepEqual(props, {});
  post({ zen: 'text', elementId: 'p1', text: 'x' });
  assert.deepEqual(sent.map((m) => m.zen), ['ready']);
});

test('bridge: text never goes into a style, a script, a picture or an SVG drawing, whatever its id', () => {
  const { p, img, post } = setup();
  const body = p.parentNode;
  const add = (tag, id, parent = body, ns) => {
    const n = new Node_(tag); n.attrs['data-zen-id'] = id; if (ns) n.namespaceURI = ns;
    n.appendChild(new Node_('#text', 'kept')); parent.appendChild(n); return n;
  };
  const SVG = 'http://www.w3.org/2000/svg';
  const style = add('STYLE', 's1'), script = add('SCRIPT', 'x1');
  const svg = add('svg', 'g1', body, SVG), inSvg = add('text', 'g2', svg, SVG);
  const svgNoNs = add('SVG', 'g3'), inSvgNoNs = add('SPAN', 'g4', svgNoNs);  // a foreign element under an svg
  for (const id of ['s1', 'x1', 'i1', 'g1', 'g2', 'g3', 'g4']) post({ zen: 'text', elementId: id, text: 'body { }' });
  for (const n of [style, script, inSvg, inSvgNoNs]) assert.deepEqual(n.shape(), ['kept']);
  assert.deepEqual(svg.shape(), ['kept', '<text>']);
  assert.deepEqual(svgNoNs.shape(), ['kept', '<SPAN>']);
  assert.deepEqual(img.childNodes, []);
  post({ zen: 'text', elementId: 'p1', text: 'still works' });
  assert.deepEqual(p.shape(), ['still works']);
});
