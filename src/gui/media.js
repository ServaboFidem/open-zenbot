// Media in a bot's reply. The reply is markdown; this pulls out what should show as a card (images,
// video, PDFs, with their "Photo by … on Pexels" credits) and takes it out of the text so nothing
// shows twice. Previews load through the harness's /media route, so the page's CSP names only the
// harness; a host off the trusted list loads only once the user clicks. Pure: no React, no DOM.
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';

const KINDS = { jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image', avif: 'image', svg: 'image', mp4: 'video', webm: 'video', mov: 'video', pdf: 'pdf' };
const CAPTION = /^(Photo|Video) by\b/;

function pathOf(url) {
  try { return new URL(url).pathname; } catch { return ''; }
}

const extKind = name => { const m = /\.([a-z0-9]+)$/i.exec(name || ''); return m ? KINDS[m[1].toLowerCase()] || null : null; };

/** 'image' | 'video' | 'pdf' | null, from the URL's path (never its query). */
export const mediaKind = url => extKind(pathOf(url));

/** The URL's last path segment, decoded; 'download' when there is none. */
export function fileName(url) {
  const seg = pathOf(url).split('/').pop() || '';
  let name = seg;
  try { name = decodeURIComponent(seg); } catch { /* keep it encoded */ }
  return name || 'download';
}

// Only web URLs become cards or caption links; a javascript: or data: URL stays text.
const isWeb = url => /^https?:\/\//i.test(url || '');

const textOf = node => (node.type === 'text' || node.type === 'inlineCode' ? node.value : (node.children || []).map(textOf).join(''));

function segments(nodes) {
  const out = [];
  for (const node of nodes) {
    const text = textOf(node).replace(/\s+/g, ' ').trim();
    if (!text) continue;
    out.push(node.type === 'link' && isWeb(node.url) ? { text, href: node.url } : { text });
  }
  return out;
}

// A node that becomes a card, or null. `alt` is the default caption, dropped before items leave.
function partOf(node) {
  if (node.type === 'image') return !isWeb(node.url) ? null : { kind: mediaKind(node.url) === 'video' ? 'video' : 'image', src: node.url, href: node.url, alt: node.alt || '' };
  if (node.type !== 'link' || !isWeb(node.url)) return null;
  const only = node.children.length === 1 && node.children[0].type === 'image' && isWeb(node.children[0].url) ? node.children[0] : null;
  if (only) {
    return mediaKind(node.url) === 'video'
      ? { kind: 'video', src: node.url, poster: only.url, href: node.url, alt: only.alt || '' }
      : { kind: 'image', src: only.url, href: node.url, alt: only.alt || '' };
  }
  const kind = mediaKind(node.url);
  if (!kind) return null;
  const text = textOf(node).trim();
  return { kind, src: node.url, href: node.url, alt: text && text !== node.url ? text : fileName(node.url) };
}

const item = part => ({ ...part, name: fileName(part.src), caption: [], local: false });

// The cards a paragraph holds, and whether the paragraph is nothing but cards and their credits.
function scanParagraph(para) {
  const found = [];
  let current = null, caption = null, other = false;
  const flush = () => { if (caption && current) current.caption = segments(caption); caption = null; };
  for (const node of para.children) {
    const part = partOf(node);
    if (part) { flush(); current = item(part); found.push(current); continue; }
    if (caption) { caption.push(node); continue; }
    if (current && node.type === 'text' && CAPTION.test(node.value.trim())) { caption = [node]; continue; }
    if (node.type === 'break' || (node.type === 'text' && !node.value.trim())) continue;
    other = true;
  }
  flush();
  return { found, removable: found.length > 0 && !other };
}

const isCaption = node => node?.type === 'paragraph' && node.children[0]?.type === 'text' && CAPTION.test(node.children[0].value.trim());

function walk(parent, items) {
  const kept = [];
  const kids = parent.children;
  for (let i = 0; i < kids.length; i++) {
    const node = kids[i];
    if (node.type === 'paragraph') {
      const { found, removable } = scanParagraph(node);
      items.push(...found);
      const last = found.at(-1);
      if (removable && last && !last.caption.length && isCaption(kids[i + 1])) { last.caption = segments(kids[i + 1].children); i++; }
      if (!removable) kept.push(node);
      continue;
    }
    if (node.type === 'list' || node.type === 'listItem' || node.type === 'blockquote') {
      walk(node, items);
      if (!node.children.length) continue;  // emptied into cards
    }
    kept.push(node);
  }
  parent.children = kept;
}

/** The reply's media as cards, in order. Mutates `tree`: what became a card leaves the text. */
export function splitMedia(tree) {
  const items = [];
  walk(tree, items);
  return items.map(({ alt, ...it }) => (it.caption.length ? it : { ...it, caption: [{ text: alt || it.name }] }));
}

/** The remark plugin the reply renderer runs, so the text it shows lacks what the cards show. */
export const remarkMediaCards = () => tree => { splitMedia(tree); };

/** The cards for a reply's markdown. */
export const mediaItems = markdown => (markdown ? splitMedia(unified().use(remarkParse).use(remarkGfm).parse(markdown)) : []);

/** A card for a file a bot attached ({id, title, name}), served from the harness at `url`. */
export function attachmentItem(att, url) {
  const name = att.name || '';
  const kind = /\.html?$/i.test(name) ? 'diagram' : extKind(name) || 'file';
  return { kind, src: url, href: url, name, caption: [{ text: att.title || name }], local: true };
}

/** Where a bot-made page's thumbnail frame loads from: the harness serves it network-closed, since it
 *  runs as the reply renders. */
export const previewFrameUrl = src => `${src}${src.includes('?') ? '&' : '?'}preview=1`;

/** Where the viewer frames a bot-made page: opened with the single-use token it asked for on the user's
 *  click, or `src` itself (which the harness serves network-closed) while it waits or when none came. */
export const viewerFrameUrl = (src, token) => (token ? `${src}${src.includes('?') ? '&' : '?'}open=${encodeURIComponent(token)}` : src);

/** The URL's host is a trusted host or a subdomain of one. */
export function isTrusted(url, hosts) {
  let host = '';
  try { host = new URL(url).hostname.toLowerCase(); } catch { return false; }
  return !!host && (hosts || []).some(h => host === h || host.endsWith('.' + h));
}

const viaHarness = (base, url) => `${base}/media?url=${encodeURIComponent(url)}`;

/** Where a remote preview loads from, or null while it waits for the user's click. */
export function previewUrl(url, { base, trusted, unlocked }) {
  if (isTrusted(url, trusted)) return viaHarness(base, url);
  return unlocked?.has(url) ? viaHarness(base, url) + '&ok=1' : null;
}

/** What Save… fetches: an attachment as served, remote media with the click flag (Save… is a click). */
export const saveUrl = (item, base) => (item.local ? item.src : viaHarness(base, item.src) + '&ok=1');
