import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { registerTelnyxTexts } from '../../src/electron/telnyx-texts.cjs';

const OFF = { phone: '', lead_minutes: 30, evening: '20:00', before: false, rundown: false };

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zen-texts-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const handlers = new Map();
  const frame = { url: 'app://openzenbot/index.html' };
  const sender = { mainFrame: frame };
  const event = { sender, senderFrame: frame };
  registerTelnyxTexts({ handle: (id, fn) => handlers.set(id, fn) }, new Set([sender]), dir);
  return { dir, event, read: handlers.get('telnyx-texts:get'), save: handlers.get('telnyx-texts:save') };
}

test('only the workspace main frame can read or change where texts go', t => {
  const { dir, event, read, save } = fixture(t);
  for (const bad of [{ ...event, senderFrame: { url: 'app://openzenbot/index.html' } }, { ...event, sender: { mainFrame: event.senderFrame } }]) {
    assert.throws(() => read(bad), /allowed/);
    assert.throws(() => save(bad, { ...OFF, phone: '+15551234567' }), /allowed/);
  }
  event.senderFrame.url = 'app://openzenbot/setup.html';
  assert.throws(() => read(event), /allowed/);
  assert.deepEqual(fs.readdirSync(dir), []);
});

test('a phone typed with spaces, brackets and dashes is saved in E.164', t => {
  const { dir, event, read, save } = fixture(t);
  assert.deepEqual(read(event), OFF);
  const saved = save(event, { phone: ' +1 (555) 123-4567 ', lead_minutes: 15, evening: '21:30', before: true, rundown: true, extra: 'x' });
  const expected = { phone: '+15551234567', lead_minutes: 15, evening: '21:30', before: true, rundown: true };
  assert.deepEqual(saved, expected);
  const file = path.join(dir, 'telnyx_texts.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), expected);
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.deepEqual(read(event), expected);
  assert.deepEqual(fs.readdirSync(dir), ['telnyx_texts.json']);
});

test('each field is checked with its own sentence', t => {
  const { event, save } = fixture(t);
  const on = { ...OFF, phone: '+15551234567', before: true };
  for (const phone of ['5551234567', '+0551234567', '+1555', '+1555123456789012', '+1555abc4567']) {
    assert.throws(() => save(event, { ...on, phone }), /country code/);
  }
  for (const lead_minutes of [45, '30', true, null]) assert.throws(() => save(event, { ...on, lead_minutes }), /reminder time/);
  for (const evening of ['24:00', '8:00', '', null]) assert.throws(() => save(event, { ...on, evening }), /evening time/);
  for (const before of [1, 'yes', null]) assert.throws(() => save(event, { ...on, before }));
  assert.throws(() => save(event, { ...OFF, rundown: true }), /turn on texts/);
  assert.deepEqual(save(event, OFF), OFF);  // texts off with no phone is fine
});

test('a damaged file is reported, a missing one reads as off', t => {
  const { dir, event, read } = fixture(t);
  fs.writeFileSync(path.join(dir, 'telnyx_texts.json'), '{bad');
  assert.throws(() => read(event), /could not be read/);
});
