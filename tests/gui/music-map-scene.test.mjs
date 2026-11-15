import test from 'node:test';
import assert from 'node:assert/strict';
import { HOME_CAM, toScreen, toWorld, zoomAt, panBy, easeCam, clampZoom, pick, placeLabels, familyCentres, curveControl, curvePoint, ownedRadius } from '../../src/gui/music/mapScene.js';

const W = 800, H = 600;
const near = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;

test('the home camera fits the 1000 x 750 world into the canvas', () => {
  assert.deepEqual(toScreen(HOME_CAM, W, H, 500, 375), { x: 400, y: 300 });
  assert.deepEqual(toScreen(HOME_CAM, W, H, 0, 0), { x: 0, y: 0 });
  assert.deepEqual(toScreen(HOME_CAM, W, H, 1000, 750), { x: 800, y: 600 });
});

test('screen and world coordinates round trip at any zoom', () => {
  const cam = { cx: 310, cy: 222, z: 3.3 };
  const s = toScreen(cam, W, H, 123, 456);
  const w = toWorld(cam, W, H, s.x, s.y);
  assert.ok(near(w.x, 123) && near(w.y, 456));
});

test('zooming at a point keeps that point under the cursor', () => {
  const before = toWorld(HOME_CAM, W, H, 120, 90);
  const cam = zoomAt(HOME_CAM, W, H, 2, 120, 90);
  const after = toWorld(cam, W, H, 120, 90);
  assert.equal(cam.z, 2);
  assert.ok(near(before.x, after.x) && near(before.y, after.y));
});

test('zoom is clamped and zooming without a point keeps the centre', () => {
  assert.equal(clampZoom(100), 8);
  assert.equal(clampZoom(0.01), 0.6);
  const cam = zoomAt({ cx: 200, cy: 100, z: 1 }, W, H, 1.5);
  assert.deepEqual(cam, { cx: 200, cy: 100, z: 1.5 });
});

test('panning moves the world with the pointer', () => {
  const cam = panBy(HOME_CAM, W, H, 80, -40);
  assert.deepEqual(toScreen(cam, W, H, 500, 375), { x: 480, y: 260 });
});

test('the eased camera approaches its target and snaps when close', () => {
  const target = { cx: 700, cy: 500, z: 4 };
  let cam = HOME_CAM;
  const first = easeCam(cam, target, 1 / 60, W, H);
  assert.ok(first.cx > 500 && first.cx < 700 && first.z > 1 && first.z < 4);
  for (let i = 0; i < 600; i++) cam = easeCam(cam, target, 1 / 60, W, H);
  assert.deepEqual(cam, target);
});

test('pick prefers your genres and suggestions over atlas dots, then the nearest', () => {
  const hits = [
    { name: 'atlas-close', x: 100, y: 100, r: 5, layer: 1 },
    { name: 'owned', x: 106, y: 100, r: 10, layer: 0 },
    { name: 'far', x: 300, y: 300, r: 10, layer: 0 },
  ];
  assert.equal(pick(hits, 101, 100), 'owned');
  assert.equal(pick(hits, 200, 200), null);
  assert.equal(pick([{ name: 'a', x: 0, y: 0, r: 6, layer: 1 }, { name: 'b', x: 8, y: 0, r: 6, layer: 1 }], 5, 0), 'b');
});

test('labels never overlap, flip left at the right edge and drop when off screen', () => {
  const measure = s => s.length * 7;
  const out = placeLabels([
    { name: 'first', x: 100, y: 100, r: 4 },
    { name: 'overlapping', x: 104, y: 102, r: 4 },
    { name: 'edge-genre', x: 790, y: 300, r: 4 },
    { name: 'gone', x: -50, y: 300, r: 4 },
  ], W, H, measure);
  assert.deepEqual(out.map(l => l.name), ['first', 'edge-genre']);
  assert.equal(out[0].align, 'left');
  assert.equal(out[1].align, 'right');
  assert.ok(out[1].x < 790);
});

test('family names sit over the densest cluster of each family, not in the gap between clusters', () => {
  const pts = [{ x: 0, y: 0, family: 0 }, { x: 1, y: 1, family: 0 }, { x: 0, y: 1, family: 0 }, { x: 50, y: 50, family: 0 }, { x: 100, y: 100, family: 0 }, { x: 5, y: 5, family: 2 }];
  assert.deepEqual(familyCentres(pts, 2), [{ family: 0, x: 1 / 3, y: 2 / 3 }, { family: 2, x: 5, y: 5 }]);
});

test('placed labels report their boxes so other text can avoid them', () => {
  const [l] = placeLabels([{ name: 'abc', x: 100, y: 100, r: 4 }], W, H, () => 30);
  assert.deepEqual(l.box, { x: 110, y: 91, w: 30, h: 18 });
});

test('link curves bow sideways and run from end to end', () => {
  const a = { x: 0, y: 0 }, b = { x: 100, y: 0 };
  const c = curveControl(a, b);
  assert.equal(c.x, 50);
  assert.notEqual(c.y, 0);
  assert.deepEqual(curvePoint(a, b, c, 0), a);
  assert.deepEqual(curvePoint(a, b, c, 1), b);
});

test('owned dots grow with share', () => {
  assert.ok(ownedRadius(0.2) > ownedRadius(0.01));
  assert.ok(ownedRadius(0) >= 3);
});
