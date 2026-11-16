// Media in a bot's reply: what becomes a card, what stays in the text, and where previews load from.
import test from 'node:test';
import assert from 'node:assert/strict';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { toString } from 'mdast-util-to-string';
import { attachmentItem, fileName, isTrusted, mediaItems, mediaKind, previewFrameUrl, previewUrl, saveUrl, splitMedia, viewerFrameUrl } from '../../src/gui/media.js';

const parse = md => unified().use(remarkParse).use(remarkGfm).parse(md);
const split = md => { const tree = parse(md); const items = splitMedia(tree); return { items, tree, text: toString(tree) }; };

test('kind and name come from the path, not the query', () => {
  const u = 'https://images.pexels.com/photos/1/pexels-photo-1.jpeg?auto=compress&cs=tinysrgb&h=650&w=940';
  assert.equal(mediaKind(u), 'image'); assert.equal(fileName(u), 'pexels-photo-1.jpeg');
  assert.equal(mediaKind('https://x.org/a.pdf#page=2'), 'pdf'); assert.equal(mediaKind('https://x.org/a.MP4'), 'video');
  assert.equal(mediaKind('https://x.org/article'), null);
  assert.equal(mediaKind('not a url'), null);
  assert.equal(fileName('https://x.org/'), 'download');
});

test('a pexels photo with its caption paragraph becomes one card and leaves the text', () => {
  const md = 'Two lakes:\n\n[![Still lake](https://images.pexels.com/p/1.jpeg)](https://www.pexels.com/photo/1/)\n\nPhoto by [Ann Lee](https://www.pexels.com/@ann) on Pexels\n\nWant more?';
  const { items, text } = split(md);
  assert.deepEqual(items, [{
    kind: 'image', src: 'https://images.pexels.com/p/1.jpeg', href: 'https://www.pexels.com/photo/1/', name: '1.jpeg',
    caption: [{ text: 'Photo by' }, { text: 'Ann Lee', href: 'https://www.pexels.com/@ann' }, { text: 'on Pexels' }], local: false,
  }]);
  assert.match(text, /Two lakes:/); assert.match(text, /Want more\?/); assert.doesNotMatch(text, /Photo by|Still lake/);
});

test('caption on the next line of the same paragraph works too', () => {
  const { items, tree } = split('[![a](https://i.x/1.jpg)](https://p.x/1)\nPhoto by X on Pexels');
  assert.equal(items.length, 1);
  assert.deepEqual(items[0].caption, [{ text: 'Photo by X on Pexels' }]);
  assert.deepEqual(tree.children, []);
});

test('a numbered list of results empties into cards and the list goes', () => {
  const md = 'Here:\n\n1. [![a](https://i.x/1.jpg)](https://p.x/1)\n   Photo by [A](https://u.x/a) on Pexels\n2. [![b](https://i.x/2.jpg)](https://p.x/2)\n   Photo by B on Pexels';
  const { items, tree } = split(md);
  assert.deepEqual(items.map(i => i.src), ['https://i.x/1.jpg', 'https://i.x/2.jpg']);
  assert.deepEqual(items[0].caption, [{ text: 'Photo by' }, { text: 'A', href: 'https://u.x/a' }, { text: 'on Pexels' }]);
  assert.deepEqual(tree.children.map(n => n.type), ['paragraph']);
});

test('a poster wrapped in a video link is a video card', () => {
  const { items } = split('[![clip](https://i.x/p.jpg)](https://videos.pexels.com/v/1.mp4)\n\nVideo by [Bo](https://u.x/bo) on Pexels');
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, 'video'); assert.equal(items[0].src, 'https://videos.pexels.com/v/1.mp4');
  assert.equal(items[0].poster, 'https://i.x/p.jpg'); assert.equal(items[0].href, 'https://videos.pexels.com/v/1.mp4');
  assert.equal(items[0].name, '1.mp4'); assert.equal(items[0].caption[0].text, 'Video by');
});

test('a media link mid-sentence stays in the text and also gets a card', () => {
  const { items, text } = split('See [the report](https://x.org/r.pdf) for details.');
  assert.deepEqual(items, [{ kind: 'pdf', src: 'https://x.org/r.pdf', href: 'https://x.org/r.pdf', name: 'r.pdf', caption: [{ text: 'the report' }], local: false }]);
  assert.equal(text, 'See the report for details.');
});

test('a bare pdf link on its own line becomes a card named after the file', () => {
  const { items, tree } = split('https://x.org/files/r%20one.pdf');
  assert.equal(items[0].name, 'r one.pdf'); assert.deepEqual(items[0].caption, [{ text: 'r one.pdf' }]);
  assert.deepEqual(tree.children, []);
});

test('an image without a caption is captioned with its alt text', () => {
  const { items, tree } = split('![A red barn](https://i.x/barn.webp)');
  assert.deepEqual(items, [{ kind: 'image', src: 'https://i.x/barn.webp', href: 'https://i.x/barn.webp', name: 'barn.webp', caption: [{ text: 'A red barn' }], local: false }]);
  assert.deepEqual(tree.children, []);
});

test('an ordinary link is not media', () => {
  const { items, text } = split('[docs](https://x.org/guide)');
  assert.deepEqual(items, []); assert.equal(text, 'docs');
});

test('mediaItems parses the markdown itself', () => {
  assert.deepEqual(mediaItems('![a](https://i.x/1.png)\n\nand [b](https://x.org/b.pdf)').map(i => i.kind), ['image', 'pdf']);
  assert.deepEqual(mediaItems(''), []);
});

test('attachments map by name', () => {
  const kinds = ['flow.html', 'r.pdf', 'a.png', 'clip.webm', 'notes.docx'].map(n => attachmentItem({ id: 'x', title: 'T ' + n, name: n }, 'http://h/a/' + n));
  assert.deepEqual(kinds.map(i => i.kind), ['diagram', 'pdf', 'image', 'video', 'file']);
  assert.deepEqual(kinds[0], { kind: 'diagram', src: 'http://h/a/flow.html', href: 'http://h/a/flow.html', name: 'flow.html', caption: [{ text: 'T flow.html' }], local: true });
});

test('trust matches a host exactly or as a subdomain', () => {
  assert.ok(isTrusted('https://images.pexels.com/a.png', ['images.pexels.com']));
  assert.ok(isTrusted('https://a.example.com/a.png', ['example.com']));
  assert.ok(!isTrusted('https://images.pexels.com.evil.net/a.png', ['images.pexels.com']));
  assert.ok(!isTrusted('nonsense', ['example.com']));
});

test('preview urls go through the harness and only trusted or unlocked hosts load', () => {
  const ctx = { base: 'http://127.0.0.1:8765', trusted: ['images.pexels.com'], unlocked: new Set(['https://cdn.x.net/a.png']) };
  assert.equal(previewUrl('https://images.pexels.com/p/1.jpeg', ctx), 'http://127.0.0.1:8765/media?url=https%3A%2F%2Fimages.pexels.com%2Fp%2F1.jpeg');
  assert.equal(previewUrl('https://cdn.x.net/a.png', ctx), 'http://127.0.0.1:8765/media?url=https%3A%2F%2Fcdn.x.net%2Fa.png&ok=1');
  assert.equal(previewUrl('https://cdn.x.net/b.png', ctx), null);
});

test('save urls always carry the click', () => {
  const remote = { src: 'https://images.pexels.com/p/1.jpeg', local: false };
  assert.equal(saveUrl(remote, 'http://h'), 'http://h/media?url=https%3A%2F%2Fimages.pexels.com%2Fp%2F1.jpeg&ok=1');
  assert.equal(saveUrl({ src: 'http://h/rooms/r/attachments/a', local: true }, 'http://h'), 'http://h/rooms/r/attachments/a');
});

test('only http(s) urls become cards or caption links', () => {
  const tree = parse('![x](javascript:alert(1)//a.png)\n\n[y](data:application/pdf;base64,AAAA/r.pdf)\n\n![ok](https://i.x/1.png)\n\nPhoto by [Z](javascript:alert(2)) on Pexels');
  const items = splitMedia(tree);
  assert.deepEqual(items.map(i => i.src), ['https://i.x/1.png']);
  assert.deepEqual(items[0].caption, [{ text: 'Photo by' }, { text: 'Z' }, { text: 'on Pexels' }]);
});

test('a diagram thumbnail asks the harness for the network-closed preview; Save… fetches the file as served', () => {
  const item = attachmentItem({ id: 'a1', title: 'Flow', name: 'flow.html' }, 'http://127.0.0.1:8765/rooms/r1/attachments/a1');
  assert.equal(previewFrameUrl(item.src), 'http://127.0.0.1:8765/rooms/r1/attachments/a1?preview=1');
  assert.equal(previewFrameUrl('http://127.0.0.1:8765/x?y=1'), 'http://127.0.0.1:8765/x?y=1&preview=1');
  assert.equal(saveUrl(item, 'http://127.0.0.1:8765'), item.src);
});

test('the viewer frames a page opened with its token, and the closed page until it has one', () => {
  const src = 'http://127.0.0.1:8765/conversations/c1/attachments/a1';
  assert.equal(viewerFrameUrl(src, 'tok-_9'), `${src}?open=tok-_9`);
  assert.equal(viewerFrameUrl(src, 'a b&c'), `${src}?open=a%20b%26c`);
  assert.equal(viewerFrameUrl('http://h/x?y=1', 't'), 'http://h/x?y=1&open=t');
  for (const none of ['', null, undefined]) assert.equal(viewerFrameUrl(src, none), src);
});
