// Preview cards for the media in a bot's reply: images and video in a grid with Save…, Open and Copy
// link under each; PDFs, diagrams and other files as compact cards with Save… and View; a viewer that
// shows an item large inside the app. Remote media loads through the harness (media.js previewUrl);
// a host off the trusted list waits for the user's click, and that click unlocks only that URL, for
// as long as the conversation is open. Bot-made pages are framed sandboxed, never same-origin, and
// network-closed; only the viewer, once the user opens one, asks the harness for a single-use open token.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Spinner } from '@fluentui/react-components';
import { ArrowDownload20Regular, Copy20Regular, Dismiss24Regular, DocumentPdf24Regular, DocumentText24Regular, Eye20Regular, Flowchart24Regular, Image24Regular, Open20Regular, Video24Regular } from '@fluentui/react-icons';
import { previewFrameUrl, previewUrl, saveUrl, viewerFrameUrl } from './media.js';
import { loadPdf, renderPage, usingPdf } from './pdf.js';
import { createApi, harnessUrl } from './api';

const DEFAULT_TRUSTED = ['images.pexels.com', 'videos.pexels.com'];
const MAX_VIEWER_PAGES = 50;

export const MediaContext = createContext(null);

/** Where previews load from, which hosts load at once, and which URLs the user has unlocked in this conversation. */
export function MediaProvider({ base, trusted, notify, scope, children }) {
  const [unlocked, setUnlocked] = useState(() => new Set());
  useEffect(() => { setUnlocked(new Set()); }, [scope]);  // a click unlocks a URL only while its conversation is open
  const unlock = useCallback(urls => setUnlocked(prev => {
    const next = new Set(prev);
    urls.filter(Boolean).forEach(u => next.add(u));
    return next;
  }), []);
  const value = useMemo(() => ({ base, trusted: trusted || DEFAULT_TRUSTED, unlocked, unlock, notify: notify || (() => {}) }), [base, trusted, unlocked, unlock, notify]);
  return <MediaContext.Provider value={value}>{children}</MediaContext.Provider>;
}

const FALLBACK = { base: harnessUrl(), trusted: DEFAULT_TRUSTED, unlocked: new Set(), unlock: () => {}, notify: () => {} };
const useMedia = () => useContext(MediaContext) || FALLBACK;

const srcOf = (url, item, ctx) => (!url ? null : item.local ? url : previewUrl(url, ctx));
const hostOf = url => { try { return new URL(url).hostname; } catch { return 'another site'; } };
const captionText = item => item.caption.map(s => s.text).join(' ');
const KIND_ICON = { image: Image24Regular, video: Video24Regular, pdf: DocumentPdf24Regular, diagram: Flowchart24Regular, file: DocumentText24Regular };

async function saveItem(item, ctx) {
  const url = saveUrl(item, ctx.base);
  const bridge = typeof window !== 'undefined' ? window.openzenbot?.saveMedia : null;
  if (bridge) {
    const r = await bridge(url, item.name);
    if (r?.saved) ctx.notify(`Saved to ${r.saved}`);
    else if (r?.error) ctx.notify(r.error);
    return;
  }
  // no desktop bridge (the GUI in a plain browser): an ordinary download
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`The file could not be fetched (${res.status}).`);
    const href = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement('a'), { href, download: item.name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (e) {
    ctx.notify(e.message || 'The file could not be saved.');
  }
}

function Caption({ item }) {
  return <div className="media-caption">{item.caption.map((s, i) => <React.Fragment key={i}>{i > 0 && ' '}{s.href ? <a href={s.href} target="_blank" rel="noopener noreferrer">{s.text}</a> : s.text}</React.Fragment>)}</div>;
}

function SaveButton({ item, primary = true }) {
  const ctx = useMedia();
  return <Button size="small" appearance={primary ? 'primary' : 'secondary'} icon={<ArrowDownload20Regular/>} onClick={() => saveItem(item, ctx)}>Save…</Button>;
}

function Locked({ kind, item, label, onLoad }) {
  return <div className="media-locked"><span>{kind === 'video' ? 'Video' : kind === 'image' ? 'Image' : 'File'} from {hostOf(item.src)}</span><Button size="small" onClick={onLoad}>{label}</Button></div>;
}

function VisualCard({ item, onView }) {
  const ctx = useMedia();
  const [failed, setFailed] = useState(false);
  const src = srcOf(item.src, item, ctx);
  const poster = srcOf(item.poster, item, ctx);
  useEffect(() => setFailed(false), [src]);
  let preview;
  if (!src) preview = <Locked kind={item.kind} item={item} label={item.kind === 'video' ? 'Load video' : 'Load image'} onLoad={() => ctx.unlock([item.src, item.poster])}/>;
  else if (failed) preview = <div className="media-placeholder">Couldn't load this {item.kind}</div>;
  else if (item.kind === 'video') preview = <video className="media-preview" src={src} poster={poster || undefined} controls preload="none" onError={() => setFailed(true)}/>;
  else preview = <button type="button" className="media-preview-button" onClick={onView} aria-label={`View ${captionText(item)}`}><img className="media-preview" src={src} alt={captionText(item)} loading="lazy" onError={() => setFailed(true)}/></button>;
  return <figure className="media-card">
    {preview}
    <Caption item={item}/>
    <div className="media-toolbar">
      <SaveButton item={item}/>
      <Button size="small" as="a" href={item.href} target="_blank" rel="noopener noreferrer" icon={<Open20Regular/>}>Open</Button>
      <Button size="small" icon={<Copy20Regular/>} onClick={async () => { try { await navigator.clipboard.writeText(item.href); ctx.notify('Link copied'); } catch { ctx.notify('Could not copy the link.'); } }}>Copy link</Button>
    </div>
  </figure>;
}

function PdfThumb({ url, onPages }) {
  const canvas = useRef(null);
  const [state, setState] = useState('loading');
  useEffect(() => {
    let alive = true;
    setState('loading');
    // the drawn page stays on the canvas; the document (and its worker) goes once it is drawn
    usingPdf(() => loadPdf(url), async d => {
      onPages(d.numPages);
      await renderPage(d, 1, canvas.current, 120);
      if (alive) setState('ready');
    }, () => alive).catch(() => { if (alive) setState('failed'); });
    return () => { alive = false; };
  }, [url]);
  if (state === 'failed') return <div className="media-thumb icon"><DocumentPdf24Regular/></div>;
  return <div className="media-thumb pdf">{state === 'loading' && <Spinner size="tiny"/>}<canvas ref={canvas} aria-hidden="true"/></div>;
}

function DocCard({ item, onView }) {
  const ctx = useMedia();
  const [pages, setPages] = useState(0);
  const src = srcOf(item.src, item, ctx);
  const Icon = KIND_ICON[item.kind] || DocumentText24Regular;
  let thumb;
  if (item.kind === 'pdf' && src) thumb = <PdfThumb url={src} onPages={setPages}/>;
  else if (item.kind === 'diagram' && src) thumb = <div className="media-thumb diagram"><iframe sandbox="allow-scripts" src={previewFrameUrl(src)} tabIndex={-1} loading="lazy" title="" aria-hidden="true"/></div>;
  else thumb = <div className="media-thumb icon"><Icon/></div>;
  const kindLabel = item.kind === 'pdf' ? `PDF${pages ? ` · ${pages} ${pages === 1 ? 'page' : 'pages'}` : ''}` : item.kind === 'diagram' ? 'Diagram' : 'File';
  const view = () => { if (!src) ctx.unlock([item.src]); onView(); };
  return <div className="media-card doc">
    <div className="media-doc-row">
      {item.kind === 'file' ? thumb : <button type="button" className="media-thumb-button" onClick={view} aria-label={`View ${item.name}`}>{thumb}</button>}
      <div className="media-doc-meta"><strong>{item.name}</strong><span>{kindLabel}</span>{item.caption.length > 0 && captionText(item) !== item.name && <Caption item={item}/>}{!src && item.kind === 'pdf' && <Button size="small" onClick={() => ctx.unlock([item.src])}>Load preview</Button>}</div>
    </div>
    <div className="media-toolbar">
      <SaveButton item={item}/>
      {item.kind !== 'file' && <Button size="small" icon={<Eye20Regular/>} onClick={view}>View</Button>}
    </div>
  </div>;
}

function PdfPages({ url }) {
  const box = useRef(null);
  const [state, setState] = useState({ pages: 0, error: '' });
  useEffect(() => {
    let alive = true;
    usingPdf(() => loadPdf(url), async d => {
      const count = Math.min(d.numPages, MAX_VIEWER_PAGES);
      setState({ pages: count, error: '' });
      const width = Math.max(240, Math.min(900, (box.current?.clientWidth || 800) - 8));
      await new Promise(r => requestAnimationFrame(r));  // the canvases exist once the page count renders
      for (let n = 1; n <= count && alive; n++) await renderPage(d, n, box.current.querySelector(`canvas[data-page="${n}"]`), width);
    }, () => alive).catch(e => { if (alive) setState({ pages: 0, error: e.message || 'The PDF could not be shown.' }); });
    return () => { alive = false; };
  }, [url]);
  if (state.error) return <p className="media-viewer-note">{state.error}</p>;
  return <div className="media-viewer-pages" ref={box}>{!state.pages && <Spinner/>}{Array.from({ length: state.pages }, (_, i) => <canvas key={i} data-page={i + 1} aria-label={`Page ${i + 1}`}/>)}</div>;
}

// The user opened this page: ask for its single-use token. Until it comes, or if the ask fails, the page stays closed.
function useOpenToken(src, wanted, base) {
  const [token, setToken] = useState({ src: null, value: '' });
  useEffect(() => {
    setToken({ src: null, value: '' });  // a token is spent once used: each opening asks afresh
    if (!wanted || !src) return undefined;
    let alive = true;
    createApi({ baseUrl: base }).openToken(src).then(value => { if (alive) setToken({ src, value }); }, () => {});
    return () => { alive = false; };
  }, [src, wanted, base]);
  return token.src === src ? token.value : '';
}

function MediaViewer({ item, onClose }) {
  const ctx = useMedia();
  const src = item ? srcOf(item.src, item, ctx) : null;
  const poster = item ? srcOf(item.poster, item, ctx) : null;
  const token = useOpenToken(src, item?.kind === 'diagram' && item.local, ctx.base);
  let body = null;
  if (item && !src) body = <p className="media-viewer-note">Loading…</p>;
  else if (item?.kind === 'image') body = <img className="media-viewer-image" src={src} alt={captionText(item)}/>;
  else if (item?.kind === 'video') body = <video className="media-viewer-image" src={src} poster={poster || undefined} controls autoPlay/>;
  else if (item?.kind === 'pdf') body = <PdfPages url={src}/>;
  else if (item?.kind === 'diagram') body = <iframe className="media-viewer-frame" sandbox="allow-scripts" src={item.local ? viewerFrameUrl(src, token) : src} title={item.name}/>;
  return <Dialog open={!!item} onOpenChange={(e, d) => { if (!d.open) onClose(); }}>
    <DialogSurface className="media-viewer">
      {item && <DialogBody>
        <DialogTitle action={<Button appearance="subtle" aria-label="Close" icon={<Dismiss24Regular/>} onClick={onClose}/>}>{item.kind === 'image' || item.kind === 'video' ? captionText(item) : item.name}</DialogTitle>
        <DialogContent className="media-viewer-content">{body}</DialogContent>
        <DialogActions><SaveButton item={item}/><Button onClick={onClose}>Close</Button></DialogActions>
      </DialogBody>}
    </DialogSurface>
  </Dialog>;
}

/** The cards for one message, in the order the media appeared, with one viewer. */
export function MediaGrid({ items }) {
  const [viewing, setViewing] = useState(null);
  if (!items?.length) return null;
  return <div className="media-grid">
    {items.map((item, i) => (item.kind === 'image' || item.kind === 'video'
      ? <VisualCard key={i} item={item} onView={() => setViewing(item)}/>
      : <DocCard key={i} item={item} onView={() => setViewing(item)}/>))}
    <MediaViewer item={viewing} onClose={() => setViewing(null)}/>
  </div>;
}
