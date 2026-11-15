// The renderer starts Sentry only when the preload set up its IPC bridge, and propagates traces to the harness only.
import test from 'node:test';
import assert from 'node:assert/strict';
import { startTelemetry } from '../../src/gui/telemetry.js';

function fakeSentry() {
  const calls = [];
  return { calls, init: o => calls.push(o), browserTracingIntegration: () => ({ name: 'BrowserTracing' }) };
}

test('no ipc bridge: telemetry stays off', () => {
  const sentry = fakeSentry();
  assert.equal(startTelemetry({ win: {}, harness: 'http://127.0.0.1:8765', sentry }), false);
  assert.deepEqual(sentry.calls, []);
});

test('ipc bridge: traces go to the harness and nowhere else', () => {
  const sentry = fakeSentry();
  assert.equal(startTelemetry({ win: { __SENTRY_IPC__: {} }, harness: 'http://127.0.0.1:8765', sentry }), true);
  const [opts] = sentry.calls;
  assert.equal(opts.tracesSampleRate, 1.0);
  assert.equal(opts.traceLifecycle, 'static');
  assert.equal(opts.sendDefaultPii, false);
  assert.deepEqual(opts.tracePropagationTargets, ['http://127.0.0.1:8765']);
  assert.deepEqual(opts.integrations.map(i => i.name), ['BrowserTracing']);
});
