// Pure geometry for the taste map canvas: camera, hit testing, label placement. No DOM, so node tests can run it.
// The world is 1000 x 750; atlas points (0-1) land inside it with a margin. The camera is a centre and a zoom,
// where zoom 1 fits the whole world into the canvas.

export const WORLD = { w: 1000, h: 750 };
export const HOME_CAM = Object.freeze({ cx: 500, cy: 375, z: 1 });
export const ZOOM_MIN = 0.6;
export const ZOOM_MAX = 8;
export const wx = p => p * 900 + 50;
export const wy = p => p * 650 + 50;

export const clampZoom = z => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
export const baseScale = (W, H) => Math.min(W / WORLD.w, H / WORLD.h);
const scaleOf = (cam, W, H) => baseScale(W, H) * cam.z;

export function toScreen(cam, W, H, x, y) {
  const k = scaleOf(cam, W, H);
  return { x: W / 2 + (x - cam.cx) * k, y: H / 2 + (y - cam.cy) * k };
}

export function toWorld(cam, W, H, sx, sy) {
  const k = scaleOf(cam, W, H);
  return { x: cam.cx + (sx - W / 2) / k, y: cam.cy + (sy - H / 2) / k };
}

// Zoom by factor, keeping the world point under (sx, sy) where it is; no point keeps the centre.
export function zoomAt(cam, W, H, factor, sx, sy) {
  const z = clampZoom(cam.z * factor);
  if (sx == null || sy == null) return { cx: cam.cx, cy: cam.cy, z };
  const p = toWorld(cam, W, H, sx, sy);
  const k = baseScale(W, H) * z;
  return { cx: p.x - (sx - W / 2) / k, cy: p.y - (sy - H / 2) / k, z };
}

export function panBy(cam, W, H, dx, dy) {
  const k = scaleOf(cam, W, H);
  return { cx: cam.cx - dx / k, cy: cam.cy - dy / k, z: cam.z };
}

// One frame of easing toward the target (frame-rate independent; zoom eases in log space). Snaps when within a
// tenth of a pixel so the loop can go idle.
export function easeCam(cam, target, dt, W, H) {
  const a = 1 - Math.exp(-dt * 10);
  const z = cam.z * Math.pow(target.z / cam.z, a);
  const next = { cx: cam.cx + (target.cx - cam.cx) * a, cy: cam.cy + (target.cy - cam.cy) * a, z };
  const k = scaleOf(target, W, H);
  const settled = Math.abs(next.cx - target.cx) * k < 0.1 && Math.abs(next.cy - target.cy) * k < 0.1
    && Math.abs(Math.log(next.z / target.z)) < 1e-3;
  return settled ? { cx: target.cx, cy: target.cy, z: target.z } : next;
}

export const camEqual = (a, b) => a.cx === b.cx && a.cy === b.cy && a.z === b.z;

// hits: [{name, x, y, r, layer}] in screen px; layer 0 (your genres, suggestions) beats layer 1 (atlas dots).
export function pick(hits, x, y) {
  let best = null, bestLayer = Infinity, bestDist = Infinity;
  for (const h of hits) {
    const d = Math.hypot(h.x - x, h.y - y);
    if (d > h.r) continue;
    if (h.layer < bestLayer || (h.layer === bestLayer && d < bestDist)) { best = h.name; bestLayer = h.layer; bestDist = d; }
  }
  return best;
}

// Greedy placement in priority order: right of the dot, else left of it; drop anything off screen or overlapping.
export function placeLabels(cands, W, H, measure, { pad = 4, height = 18, gap = 6, edge = 6 } = {}) {
  const taken = [];
  const out = [];
  const seen = new Set();
  for (const c of cands) {
    if (seen.has(c.name)) continue;
    seen.add(c.name);
    if (c.x < 0 || c.y < 0 || c.x > W || c.y > H) continue;
    const w = measure(c.name);
    const y = c.y - height / 2;
    if (y < 0 || y + height > H) continue;
    let box = { x: c.x + c.r + gap, y, w, h: height }, align = 'left', lx = box.x;
    if (box.x + w > W - edge) { box = { x: c.x - c.r - gap - w, y, w, h: height }; align = 'right'; lx = c.x - c.r - gap; }
    if (box.x < edge) continue;
    if (taken.some(b => box.x < b.x + b.w + pad && box.x + box.w + pad > b.x && box.y < b.y + b.h + pad && box.y + box.h + pad > b.y)) continue;
    taken.push(box);
    out.push({ name: c.name, x: lx, y: c.y, align, box });
  }
  return out;
}

// Where to write each family's name: the middle of its densest cluster (the point with the most same-family
// neighbours within radius, averaged with those neighbours). A median can land in the gap between clusters.
export function familyCentres(points, radius = 40) {
  const groups = new Map();
  for (const p of points) {
    if (!groups.has(p.family)) groups.set(p.family, []);
    groups.get(p.family).push(p);
  }
  return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([family, ps]) => {
    let best = [];
    for (const p of ps) {
      const near = ps.filter(q => Math.hypot(q.x - p.x, q.y - p.y) <= radius);
      if (near.length > best.length) best = near;
    }
    return { family, x: best.reduce((s, q) => s + q.x, 0) / best.length, y: best.reduce((s, q) => s + q.y, 0) / best.length };
  });
}

// A link bows to one side by 18% of its length.
export function curveControl(a, b, bend = 0.18) {
  return { x: (a.x + b.x) / 2 - (b.y - a.y) * bend, y: (a.y + b.y) / 2 + (b.x - a.x) * bend };
}

export function curvePoint(a, b, c, t) {
  const u = 1 - t;
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
}

export const ownedRadius = share => 3 + Math.sqrt(Math.max(0, share)) * 26;

// A stable per-name phase (0..2π) so each dot breathes on its own beat.
export function phaseOf(name) {
  let h = 2166136261;
  for (let i = 0; i < name.length; i++) h = Math.imul(h ^ name.charCodeAt(i), 16777619);
  return ((h >>> 0) % 1000) / 1000 * Math.PI * 2;
}
