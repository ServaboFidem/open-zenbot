import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { registerMorningEmail } from '../../src/electron/morning-email.cjs';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zen-digest-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const handlers = new Map();
  const frame = { url: 'app://openzenbot/index.html' };
  const sender = { mainFrame: frame };
  const event = { sender, senderFrame: frame };
  registerMorningEmail({ handle: (id, fn) => handlers.set(id, fn) }, new Set([sender]), dir);
  return { dir, event, read: handlers.get('morning-email:get'), save: handlers.get('morning-email:save') };
}

test('only the application workspace main frame can read or change the private destination', t => {
  const { dir, event, read, save } = fixture(t);
  for (const bad of [
    { ...event, senderFrame: { url: 'app://openzenbot/index.html' } },
    { ...event, sender: { mainFrame: event.senderFrame } },
  ]) {
    assert.throws(() => read(bad), /allowed/);
    assert.throws(() => save(bad, { recipient: 'other@example.com', time: '07:00' }), /allowed/);
  }
  for (const url of ['app://openzenbot/setup.html', 'app://openzenbot/index.html.evil', 'http://localhost:8765']) {
    event.senderFrame.url = url;
    assert.throws(() => read(event), /allowed/);
    assert.throws(() => save(event, { recipient: 'other@example.com', time: '07:00' }), /allowed/);
  }
  assert.deepEqual(fs.readdirSync(dir), []);
});

test('saving and clearing writes only the one address and morning time in the settings directory', t => {
  const { dir, event, read, save } = fixture(t);
  assert.deepEqual(read(event), { recipient: '', time: '07:00' });
  save(event, { recipient: '  owner@example.com  ', time: '11:59', arbitrary: 'ignored' });
  const file = path.join(dir, 'morning_email.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { recipient: 'owner@example.com', time: '11:59' });
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  for (const recipient of ['one@example.com,two@example.com', 'bad', 'name <a@example.com>', 'x@example.com\nBcc:other@example.com']) {
    assert.throws(() => save(event, { recipient, time: '07:00' }));
  }
  for (const time of ['12:00', '6:00', '10:60', '', null]) assert.throws(() => save(event, { recipient: '', time }));
  assert.equal(read(event).recipient, 'owner@example.com');
  save(event, { recipient: '', time: '00:00' });
  assert.deepEqual(read(event), { recipient: '', time: '00:00' });
  assert.deepEqual(fs.readdirSync(dir), ['morning_email.json']);
});
