import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Input, ProgressBar, Switch } from '@fluentui/react-components';
import { Search24Regular } from '@fluentui/react-icons';
import { scanProgress } from './helpers.js';
import { HOME_CAM, WORLD, wx, wy, baseScale, toScreen, zoomAt, panBy, easeCam, camEqual, pick, placeLabels, familyCentres, curveControl, curvePoint, ownedRadius, phaseOf } from './mapScene.js';

// Bright enough to glow on the dark canvas, still readable as swatches on the white legend.
const familyColors = ['#6fa8dc', '#a58fd6', '#d9b44a', '#e889a6', '#e8935a', '#4fc1b6', '#e25c5c', '#9cc45a', '#9aa6b2'];
const BG = '#0b1018';
const LINK = '#6cb6ff';
const FONT = "12px 'Segoe UI', system-ui, sans-serif";
const FONT_BOLD = "600 12px 'Segoe UI', system-ui, sans-serif";
const FAMILY_FONT = "600 10px 'Segoe UI', system-ui, sans-serif";
const TAU = Math.PI * 2;
const SELECT_ZOOM = 2.8;

function rgb(hex) { const n = parseInt(hex.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
const rgba = (hex, a) => `rgba(${rgb(hex).join(',')},${a})`;
const mixWhite = (hex, t) => `rgb(${rgb(hex).map(c => Math.round(c + (255 - c) * t)).join(',')})`;
const colorOf = family => familyColors[family % familyColors.length];

// Soft colour pools per family, drawn once at world size and scaled with the camera.
function buildNebula(points) {
  if (typeof document === 'undefined') return null;
  const dots = document.createElement('canvas');
  dots.width = WORLD.w; dots.height = WORLD.h;
  const d = dots.getContext('2d');
  if (!d) return null;
  for (const p of points) { d.fillStyle = rgba(p.color, 0.05); d.beginPath(); d.arc(p.wx, p.wy, 20, 0, TAU); d.fill(); }
  const out = document.createElement('canvas');
  out.width = WORLD.w; out.height = WORLD.h;
  const o = out.getContext('2d');
  o.filter = 'blur(16px)';
  o.drawImage(dots, 0, 0);
  return out;
}

function draw(ctx, L, cam, size, t, measure) {
  const { W, H, dpr } = size;
  const motion = !L.reduced;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.globalAlpha = 1;
  const bg = ctx.createRadialGradient(W / 2, H * 0.45, 0, W / 2, H * 0.45, Math.max(W, H) * 0.75);
  bg.addColorStop(0, '#132033'); bg.addColorStop(1, BG);
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

  const k = baseScale(W, H) * cam.z;
  const at = (x, y) => toScreen(cam, W, H, x, y);
  const inView = (s, m = 20) => s.x > -m && s.y > -m && s.x < W + m && s.y < H + m;
  const o = at(0, 0);
  if (L.nebula) { ctx.globalAlpha = 0.95; ctx.drawImage(L.nebula, o.x, o.y, WORLD.w * k, WORLD.h * k); ctx.globalAlpha = 1; }

  const hits = [];
  const active = L.hover || L.selected;
  const dotScale = Math.max(0.8, Math.min(1 + (cam.z - 1) * 0.3, 2.6));
  const ownedR = new Map();

  // Every atlas genre: a faint twinkling star.
  for (const p of L.points) {
    if (L.owned.has(p.name)) continue;
    const s = at(p.wx, p.wy);
    if (!inView(s)) continue;
    const r = (p.core ? 1.5 : 1.1) * dotScale;
    ctx.globalAlpha = 0.5 * (motion ? 0.72 + 0.28 * Math.sin(t * 0.9 + p.phase) : 0.85);
    ctx.fillStyle = p.color;
    ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.fill();
    hits.push({ name: p.name, x: s.x, y: s.y, r: Math.max(6, r + 3), layer: 1 });
  }

  // "Because you have X": a soft curve from your genre to the suggestion, with light flowing along it.
  if (L.showSuggestions) {
    for (const g of L.related) {
      const from = L.byName.get(g.because), to = L.byName.get(g.genre);
      if (!from || !to) continue;
      const a = at(from.wx, from.wy), b = at(to.wx, to.wy);
      if (!inView(a, 400) && !inView(b, 400)) continue;
      const c = curveControl(a, b);
      const lit = active === g.genre || active === g.because;
      ctx.strokeStyle = LINK; ctx.lineWidth = lit ? 1.6 : 1.1;
      ctx.globalAlpha = lit ? 0.7 : 0.22 * (motion ? 1 + 0.2 * Math.sin(0.6 * t + to.phase) : 1);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.quadraticCurveTo(c.x, c.y, b.x, b.y); ctx.stroke();
      if (motion) {
        const n = lit ? 3 : 1;
        ctx.fillStyle = '#cfe7ff'; ctx.shadowColor = LINK; ctx.shadowBlur = 8;
        for (let i = 0; i < n; i++) {
          const u = (t * 0.2 + i / n + to.phase / TAU) % 1;
          const q = curvePoint(a, b, c, u);
          ctx.globalAlpha = Math.sin(u * Math.PI) * (lit ? 1 : 0.7);
          ctx.beginPath(); ctx.arc(q.x, q.y, lit ? 2.2 : 1.6, 0, TAU); ctx.fill();
        }
        ctx.shadowBlur = 0;
      }
    }
  }

  // Your genres: breathing orbs with a halo, sized by share; the active one glows.
  const zoomGrow = Math.min(Math.pow(Math.max(cam.z, 1), 0.35), 1.8);
  for (const g of L.mine) {
    const p = L.byName.get(g.genre);
    if (!p) continue;
    const s = at(p.wx, p.wy);
    const base = ownedRadius(g.share) * zoomGrow;
    ownedR.set(g.genre, base);
    if (!inView(s, base * 2.5)) continue;
    const r = base * (motion ? 1 + 0.06 * Math.sin(1.1 * t + p.phase) : 1);
    const on = active === g.genre;
    const dim = L.hover && !on ? 0.55 : 1;
    ctx.fillStyle = p.color;
    ctx.globalAlpha = (0.08 + (motion ? 0.035 * Math.sin(0.9 * t + 1.7 * p.phase) : 0)) * dim;
    ctx.beginPath(); ctx.arc(s.x, s.y, r * 2.1, 0, TAU); ctx.fill();
    const grad = ctx.createRadialGradient(s.x - r * 0.35, s.y - r * 0.35, r * 0.1, s.x, s.y, r);
    grad.addColorStop(0, mixWhite(p.color, 0.5)); grad.addColorStop(1, p.color);
    ctx.fillStyle = grad; ctx.globalAlpha = 0.92 * dim;
    if (on) { ctx.shadowColor = p.color; ctx.shadowBlur = L.selected === g.genre ? 26 : 16; }
    ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1; ctx.stroke();
    hits.push({ name: g.genre, x: s.x, y: s.y, r: r + 3, layer: 0 });
  }

  // Places to explore: dashed rings that slowly turn.
  if (L.showSuggestions) {
    ctx.setLineDash([3, 3]);
    for (const g of L.related) {
      const p = L.byName.get(g.genre);
      if (!p) continue;
      const s = at(p.wx, p.wy);
      if (!inView(s)) continue;
      const r = 7 * (motion ? 1 + 0.08 * Math.sin(1.3 * t + p.phase) : 1);
      ctx.lineDashOffset = motion ? -t * 6 : 0;
      ctx.globalAlpha = 1; ctx.fillStyle = rgba(LINK, 0.12);
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.fill();
      ctx.strokeStyle = LINK; ctx.lineWidth = 1.4; ctx.globalAlpha = active === g.genre ? 1 : 0.85; ctx.stroke();
      hits.push({ name: g.genre, x: s.x, y: s.y, r: 11, layer: 0 });
    }
    ctx.setLineDash([]); ctx.lineDashOffset = 0;
  }

  // Selection and hover rings.
  const ring = (name, alpha, glow) => {
    const p = L.byName.get(name);
    if (!p) return;
    const s = at(p.wx, p.wy);
    const r = (ownedR.get(name) ?? 4) + 6;
    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5; ctx.globalAlpha = alpha;
    if (glow) { ctx.shadowColor = '#ffffff'; ctx.shadowBlur = 12; }
    ctx.beginPath(); ctx.arc(s.x, s.y, r * (motion && glow ? 1 + 0.05 * Math.sin(2 * t) : 1), 0, TAU); ctx.stroke();
    ctx.shadowBlur = 0;
  };
  if (L.selected) ring(L.selected, 0.9, true);
  if (L.hover && L.hover !== L.selected) ring(L.hover, 0.45, false);

  // Genre labels: selection, hover, your biggest genres, suggestions, then atlas names once you are close in.
  const cands = [];
  const cand = (name, r) => { const p = L.byName.get(name); if (p) { const s = at(p.wx, p.wy); cands.push({ name, x: s.x, y: s.y, r }); } };
  if (L.selected) cand(L.selected, ownedR.get(L.selected) ?? 4);
  if (L.hover) cand(L.hover, ownedR.get(L.hover) ?? 4);
  for (const g of L.mine.slice(0, cam.z > 2 ? 60 : 10)) cand(g.genre, ownedR.get(g.genre) ?? 4);
  if (L.showSuggestions) for (const g of L.related) cand(g.genre, 8);
  if (cam.z > 3) for (const p of L.coreFirst) { if (cands.length > 220) break; const s = at(p.wx, p.wy); if (inView(s, 0)) cands.push({ name: p.name, x: s.x, y: s.y, r: 3 }); }
  const placed = placeLabels(cands, W, H, name => measure(name) + 2);

  // Family names float over their region when zoomed out, fade as you zoom in, and step back behind genre labels.
  const famAlpha = Math.max(0, Math.min(1, 1 - (cam.z - 1.2) / 1.3)) * 0.6;
  if (famAlpha > 0.02) {
    ctx.font = FAMILY_FONT; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    if ('letterSpacing' in ctx) ctx.letterSpacing = '1.5px';
    for (const c of L.centres) {
      const name = L.families[c.family];
      if (!name) continue;
      const text = name.toUpperCase();
      const s = at(c.wx, c.wy);
      const w = ctx.measureText(text).width;
      const clash = placed.some(({ box: b }) => s.x - w / 2 < b.x + b.w && s.x + w / 2 > b.x && s.y - 7 < b.y + b.h && s.y + 7 > b.y);
      ctx.globalAlpha = famAlpha * (clash ? 0.25 : 1); ctx.fillStyle = mixWhite(colorOf(c.family), 0.25);
      ctx.fillText(text, s.x, s.y);
    }
    if ('letterSpacing' in ctx) ctx.letterSpacing = '0px';
  }

  ctx.textBaseline = 'middle'; ctx.lineJoin = 'round';
  for (const l of placed) {
    ctx.font = l.name === L.selected ? FONT_BOLD : FONT;
    ctx.textAlign = l.align;
    ctx.globalAlpha = 1; ctx.strokeStyle = rgba(BG, 0.85); ctx.lineWidth = 4;
    ctx.strokeText(l.name, l.x, l.y);
    ctx.fillStyle = L.owned.has(l.name) ? '#eef3f8' : L.suggested.has(l.name) ? '#a9d4ff' : '#97a3b3';
    ctx.fillText(l.name, l.x, l.y);
  }
  ctx.globalAlpha = 1;
  return hits;
}

// The taste map: every atlas genre as a star, the collection's genres as orbs sized by share, suggested genres ringed.
// props: map {families, points, genres, related}, status, selected, setSelected, onScan, needsToken, onToken, panel (node for the selected genre)
export default function TasteMap({ map, status, selected, setSelected, onScan, needsToken, onToken, panel }) {
  const [hover, setHover] = useState(null);
  const [search, setSearch] = useState('');
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [zoomPct, setZoomPct] = useState(100);
  const canvasRef = useRef(null);
  const cam = useRef(HOME_CAM);
  const target = useRef(HOME_CAM);
  const size = useRef({ W: 740, H: 555, dpr: 1 });
  const hits = useRef([]);
  const live = useRef(null);
  const invalidate = useRef(() => {});
  const drag = useRef(null);
  const hoverRef = useRef(null);

  const allGenres = useMemo(() => map.points.map(([name, x, y, family, core]) => ({ name, x, y, family, core, wx: wx(x), wy: wy(y), phase: phaseOf(name), color: colorOf(family) })), [map]);
  const byName = useMemo(() => new Map(allGenres.map(g => [g.name, g])), [allGenres]);
  const coreFirst = useMemo(() => [...allGenres].sort((a, b) => (b.core ? 1 : 0) - (a.core ? 1 : 0)), [allGenres]);
  const centres = useMemo(() => familyCentres(allGenres.map(g => ({ x: g.wx, y: g.wy, family: g.family }))).map(c => ({ family: c.family, wx: c.x, wy: c.y })), [allGenres]);
  const nebula = useMemo(() => buildNebula(allGenres), [allGenres]);
  const mine = map.genres || [];
  const related = map.related || [];
  const ownedByName = useMemo(() => new Map(mine.map(g => [g.genre, g])), [mine]);
  const suggestedByName = useMemo(() => new Map(related.map(g => [g.genre, g])), [related]);
  const scan = status?.scan;
  const scanning = scan?.state === 'running';

  live.current = { ...(live.current || {}), points: allGenres, byName, coreFirst, centres, nebula, mine, related, families: map.families,
    owned: ownedByName, suggested: suggestedByName, selected, hover, showSuggestions };

  function setTarget(next) {
    target.current = next;
    setZoomPct(Math.round(next.z * 100));
    invalidate.current();
  }
  const zoomBy = factor => { const { W, H } = size.current; setTarget(zoomAt(target.current, W, H, factor)); };

  // The render loop: animates while motion is allowed, otherwise draws only when something changed.
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!ctx) return undefined;
    const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    live.current.reduced = reduced;
    const widths = new Map();
    const measure = name => {
      if (!widths.has(name)) { ctx.font = FONT_BOLD; widths.set(name, ctx.measureText(name).width); }
      return widths.get(name);
    };
    let raf = 0, last = performance.now();
    const start = last;
    const frame = now => {
      raf = 0;
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const { W, H } = size.current;
      cam.current = reduced ? target.current : easeCam(cam.current, target.current, dt, W, H);
      hits.current = draw(ctx, live.current, cam.current, size.current, reduced ? 0 : (now - start) / 1000, measure);
      if (!reduced || !camEqual(cam.current, target.current)) raf = requestAnimationFrame(frame);
    };
    invalidate.current = () => { if (!raf) raf = requestAnimationFrame(frame); };
    const obs = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      const dpr = window.devicePixelRatio || 1;
      size.current = { W: width, H: height, dpr };
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
      invalidate.current();
    });
    obs?.observe(canvas);
    const wheel = e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const { W, H } = size.current;
      setTarget(zoomAt(target.current, W, H, e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - rect.left, e.clientY - rect.top));
    };
    canvas.addEventListener('wheel', wheel, { passive: false });
    invalidate.current();
    return () => { cancelAnimationFrame(raf); obs?.disconnect(); canvas.removeEventListener('wheel', wheel); invalidate.current = () => {}; };
  }, []);
  useEffect(() => { invalidate.current(); });

  function select(name) {
    setSelected(name); setSearch('');
    const g = byName.get(name);
    if (g) setTarget({ cx: g.wx, cy: g.wy, z: SELECT_ZOOM });
  }
  const searchResults = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    return q ? allGenres.filter(g => g.name.toLocaleLowerCase().includes(q)).slice(0, 12) : [];
  }, [search, allGenres]);

  const active = hover || selected;
  const activeOwned = ownedByName.get(active);
  const activeSuggested = suggestedByName.get(active);
  const progress = scanProgress(scan);

  const local = e => { const rect = e.currentTarget.getBoundingClientRect(); return { x: e.clientX - rect.left, y: e.clientY - rect.top }; };
  const onKeyDown = e => {
    const { W, H } = size.current;
    const step = { ArrowLeft: [W * 0.1, 0], ArrowRight: [-W * 0.1, 0], ArrowUp: [0, H * 0.1], ArrowDown: [0, -H * 0.1] }[e.key];
    if (step || ['+', '=', '-'].includes(e.key)) e.preventDefault();
    if (e.key === '+' || e.key === '=') zoomBy(1.3);
    else if (e.key === '-') zoomBy(1 / 1.3);
    else if (step) setTarget(panBy(target.current, W, H, step[0], step[1]));
  };
  const onPointerDown = e => {
    drag.current = { x: e.clientX, y: e.clientY, cam: target.current, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = e => {
    if (drag.current) {
      const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
      if (Math.abs(dx) + Math.abs(dy) > 5) drag.current.moved = true;
      if (!drag.current.moved) return;
      const { W, H } = size.current;
      const next = panBy(drag.current.cam, W, H, dx, dy);
      target.current = next; cam.current = next;
      invalidate.current();
      return;
    }
    const p = local(e);
    const name = pick(hits.current, p.x, p.y);
    e.currentTarget.style.cursor = name ? 'pointer' : '';
    if (name !== hoverRef.current) { hoverRef.current = name; setHover(name); }
  };
  const onPointerUp = e => {
    const wasDrag = drag.current?.moved;
    drag.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* not captured */ }
    if (!wasDrag) { const p = local(e); const name = pick(hits.current, p.x, p.y); if (name) setSelected(name); }
  };
  const onPointerLeave = () => { if (!drag.current) { hoverRef.current = null; setHover(null); } };

  return <div className={`map-layout ${selected ? 'has-panel' : ''}`}>
    <div className="od-stack" style={{ '--od-gap': 'var(--s4)' }}>
      {needsToken && <div className="notice" role="status"><div><strong>Paste your Discogs token to get weekly picks</strong><p>The map works without one. Picks need it to check albums against your collection.</p></div><Button appearance="primary" onClick={onToken}>Add a token</Button></div>}
      {scanning && <div className="notice" role="status"><div><strong>Reading your collection</strong><p>{scan.stage === 'mapping' ? 'Matching your records to the atlas.' : 'Reading your Discogs collection.'}</p><ProgressBar value={progress.value} aria-label="Collection scan progress"/></div></div>}
      {!scanning && !mine.length && <div className="notice" role="status"><div><strong>Your collection is not on the map yet</strong><p>{scan?.state === 'failed' ? 'The last scan did not finish.' : 'Scan it once to see where your records sit.'}</p></div><Button appearance="primary" onClick={onScan}>Scan collection</Button></div>}
      <div className="map-card">
        <div className="map-toolbar"><Input className="map-search" contentBefore={<Search24Regular className="od-icon" aria-hidden="true"/>} aria-label="Search all genres" placeholder="Find a genre" value={search} onChange={(_, d) => setSearch(d.value)} onKeyDown={e => { if (e.key === 'Enter' && searchResults[0]) select(searchResults[0].name); }}/><Button aria-label="Zoom in" onClick={() => zoomBy(1.5)}>+</Button><Button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.5)}>&minus;</Button><Button onClick={() => setTarget(HOME_CAM)}>Reset</Button></div>
        {search && <div className="search-results" aria-live="polite">{searchResults.length ? searchResults.map(g => <button key={g.name} onClick={() => select(g.name)}>{g.name}</button>) : <p className="small muted">No matching genre. Try a shorter name.</p>}</div>}
        <canvas ref={canvasRef} className="map-canvas" role="img" aria-label="Your collection in a world of sound: the Soundlore genre map. Drag to pan, use zoom buttons, or search for a genre. Arrow keys pan and plus or minus zoom." aria-describedby="taste-map-desc" tabIndex={0} onKeyDown={onKeyDown} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={() => { drag.current = null; }} onPointerLeave={onPointerLeave}/>
        <p id="taste-map-desc" className="map-desc">{allGenres.length} genres grouped by musical similarity. Larger glowing dots represent your collection. Dashed rings suggest nearby genres to explore. Search provides keyboard-accessible selection.</p>
        <div className="map-info" aria-live="polite">{active ? <><strong style={{ textTransform: 'capitalize' }}>{active}</strong><span className="muted">{activeOwned ? `${(activeOwned.share * 100).toFixed(2)}% of your mapped profile` : activeSuggested ? `Explore · because you have ${activeSuggested.because}` : 'Atlas genre'}</span></> : <span className="hint">Drag to pan · scroll to zoom · select a dot to explore</span>}<span className="hint" style={{ marginLeft: 'auto' }}>{zoomPct}%</span></div>
        <div className="map-legend">{map.families.map((f, i) => <span className="legend-item" key={f}><span className="legend-swatch" style={{ '--family': colorOf(i) }}/>{f}</span>)}</div>
      </div>
      <div className="od-row" style={{ flexWrap: 'wrap', justifyContent: 'space-between' }}><span className="legend-item"><span className="legend-swatch" style={{ '--family': '#525252', width: 12, height: 12 }}/>Size = share of your mapped profile</span><Switch checked={showSuggestions} onChange={(_, d) => setShowSuggestions(d.checked)} label="Show places to explore"/></div>
    </div>
    {selected ? panel : <aside className="map-sidebar">
      <div className="summary-panel">
        <div className="od-stat"><strong className="summary-number">{(map.releases ?? 0).toLocaleString()}</strong><span className="muted small">records in your collection</span></div>
        {scan?.mapped > 0 && <p className="summary-note">{scan.mapped.toLocaleString()} records mapped to the atlas</p>}
        {mine.length > 0 && <div className="summary-list"><h3>Your strongest genres</h3>{mine.slice(0, 5).map(g => <div className="summary-row" key={g.genre}><button className="text-button" style={{ textAlign: 'left' }} onClick={() => select(g.genre)}>{g.genre}</button><strong>{(g.share * 100).toFixed(1)}%</strong><div className="bar-track"><div className="bar-fill" style={{ width: `${g.share / mine[0].share * 100}%` }}/></div></div>)}</div>}
        <p className="summary-note">Shares describe the mapped genre profile, not a count of releases.</p>
        <Button size="small" appearance="subtle" disabled={scanning} onClick={onScan}>{scanning ? 'Scanning…' : 'Rescan collection'}</Button>
      </div>
      {map.releases > 0 && <div className="summary-panel"><h3>On your shelves</h3>
        {Object.entries(map.formats || {}).sort((a, b) => b[1] - a[1]).map(([format, n]) => <div className="summary-row" key={format}><span>{format}</span><strong>{n.toLocaleString()} {n === 1 ? 'release' : 'releases'}</strong></div>)}
        <p className="summary-note">A release counts under each of its formats, so these can add up to more than your {map.releases.toLocaleString()} records.</p>
        <div className="summary-list"><h3>Release decades</h3>{Object.entries(map.decades || {}).map(([decade, n]) => <div className="summary-row" key={decade}><span>{decade === 'unknown' ? 'Unknown' : `${decade}s`}</span><span>{n.toLocaleString()}</span><div className="bar-track"><div className="bar-fill" style={{ width: `${n / Math.max(...Object.values(map.decades)) * 100}%` }}/></div></div>)}</div>
        <p className="summary-note">Each release is counted once. Releases with no year are under Unknown.</p></div>}
      {related.length > 0 && <div className="summary-panel"><h3>A little further out</h3><p className="small muted" style={{ marginBottom: 'var(--s3)' }}>Close to what you love. New to your shelf.</p><div className="explore-list">{related.slice(0, 4).map(g => <button className="explore-button" key={g.genre} onClick={() => select(g.genre)}><strong>{g.genre}</strong><span>Because you have {g.because}</span></button>)}</div></div>}
    </aside>}
  </div>;
}
