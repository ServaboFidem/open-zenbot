// The installed app's setup and start page (setup.html): plain script, no React. The main process sends the
// state ({ phase, update, setup, harness }); setup-view.js turns it into words and buttons, set here with
// textContent only. The buttons go back over the preload's narrow calls (window.openzenbot.setup, and
// updates.open() for "Get the new version", which opens only this repo's release pages).
import { viewOf } from './setup-view.js';

const bridge = window.openzenbot || {};
const api = bridge.setup;
const $ = id => document.getElementById(id);
let detailsOpen = false;
let last = null;

const LABELS = {
  'get-update': 'Get the new version',
  continue: 'Continue with this one',
  retry: 'Retry',
  details: () => (detailsOpen ? 'Hide details' : 'Show details'),
  'open-logs': 'Open log folder',
  restart: 'Restart',
};
const ACTIONS = {
  'get-update': () => bridge.updates.open(),
  continue: () => api.proceed(),
  retry: () => api.retry(),
  details: () => { detailsOpen = !detailsOpen; render(last); },
  'open-logs': () => api.openLogs(),
  restart: () => api.restartHarness(),
};

function render(state) {
  last = state;
  const v = viewOf(state);
  $('heading').textContent = v.heading;
  document.title = `OpenZenBot — ${v.heading}`;
  $('body').textContent = v.body;
  $('line').textContent = v.line || '';

  const steps = $('steps');
  steps.replaceChildren(...v.steps.map(s => {
    const li = document.createElement('li');
    li.className = s.status;
    const title = document.createElement('span');
    title.textContent = s.title;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = s.label;
    li.append(title, label);
    return li;
  }));

  const actions = $('actions');
  actions.replaceChildren(...v.actions.map((name, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    if (i === 0 && name !== 'details') b.className = 'primary';
    const label = LABELS[name];
    b.textContent = typeof label === 'function' ? label() : label;
    b.addEventListener('click', async () => {
      if (name !== 'details') b.disabled = true;
      try { await ACTIONS[name](); } catch { /* the next state says what happened */ }
      if (name !== 'details') b.disabled = false;
    });
    return b;
  }));

  // the release notes and a stopped harness's last output show as they are; setup's output behind Show details
  const detail = v.detail || '';
  const shown = v.actions.includes('details') ? detailsOpen && !!detail : !!detail;
  $('detail-box').hidden = !shown;
  $('detail-title').textContent = state && state.phase === 'update' ? "What's new" : 'Details';
  $('detail').textContent = detail;
}

if (api) {
  api.onState(render);
  api.state().then(render).catch(() => {});  // IPC keeps order: this answer is at least as new as any state sent before it
} else {
  $('body').textContent = 'This page belongs to the OpenZenBot app.';
}
