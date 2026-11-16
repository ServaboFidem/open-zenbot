// The setup page's words and buttons for the installed app, from the state the main process sends
// ({ phase, setup, update, harness }; src/electron/managed.cjs). Pure: setup.js puts the result on the page with
// textContent. phase: update (the check, then the offer of a newer release), setup (first-launch setup, the
// steps from src/electron/setup.cjs), starting (the harness is starting) or failed (the harness could not start
// or stopped). actions name buttons: get-update, continue, retry, details, open-logs, restart.

const STATUS_LABELS = { pending: 'Waiting', running: 'In progress', done: 'Done', skipped: 'Not needed', failed: 'Failed' };

const text = v => (typeof v === 'string' ? v : '');
const lines = v => (Array.isArray(v) ? v.map(String).join('\n') : text(v));

function stepsOf(setup) {
  const list = setup && Array.isArray(setup.steps) ? setup.steps : [];
  return list.map(s => ({ title: text(s && s.title), status: text(s && s.status), label: STATUS_LABELS[s && s.status] || '' }));
}

function updateView(update) {
  if (!update || !update.notify) {
    return { heading: 'Checking for a newer version', body: 'This takes a few seconds.', steps: [], actions: [] };
  }
  const have = text(update.current);
  return {
    heading: `OpenZenBot ${text(update.latest)} is available`,
    body: `${have ? `You have version ${have}. ` : ''}Nothing has been downloaded yet: get the new version now, or continue with this one.`,
    steps: [],
    actions: ['get-update', 'continue'],
    detail: text(update.notes),
  };
}

function setupView(setup) {
  const steps = stepsOf(setup);
  const failed = !!(setup && setup.error) || steps.some(s => s.status === 'failed');
  if (failed) {
    return {
      heading: 'Setup could not finish',
      body: `${text(setup && setup.error) || 'A setup step failed.'} Steps that finished are not done again when you retry.`,
      steps,
      actions: ['retry', 'details', 'open-logs'],
      detail: lines(setup && setup.detail),
    };
  }
  return {
    heading: 'Setting up OpenZenBot',
    body: 'OpenZenBot is downloading Python and its packages, and Node.js if this computer has none: up to a few gigabytes, '
      + 'with about 6 GB of free disk space needed. This happens once; later launches start straight away.',
    steps,
    actions: ['details'],
    line: text(setup && setup.line),
    detail: lines(setup && setup.detail),
  };
}

function startingView(harness) {
  return {
    heading: 'Starting OpenZenBot',
    body: harness && harness.firstStart
      ? 'The first start fetches tools and memory models, once. It can take several minutes; later starts are quick.'
      : 'This takes a few seconds.',
    steps: [],
    actions: [],
  };
}

function failedView(harness) {
  if (harness && harness.reason === 'error') {
    return {
      heading: 'OpenZenBot could not start.',
      body: 'Something went wrong while it was starting. Open the log folder to see what happened, then open OpenZenBot again.',
      steps: [],
      actions: ['open-logs'],
      detail: text(harness.detail),
    };
  }
  if (harness && harness.reason === 'port-in-use') {
    return {
      heading: 'Port 8765 is in use',
      body: 'Another program is using port 8765, which OpenZenBot needs. Close that program, then open OpenZenBot again.',
      steps: [],
      actions: ['open-logs'],
      detail: text(harness.detail),
    };
  }
  return {
    heading: 'The harness stopped',
    body: "OpenZenBot's harness is not running. Restart it, or open the log folder to see why it stopped.",
    steps: [],
    actions: ['restart', 'open-logs'],
    detail: text(harness && harness.detail),
  };
}

export function viewOf(state) {
  const s = state && typeof state === 'object' ? state : {};
  switch (s.phase) {
    case 'update': return updateView(s.update);
    case 'setup': return setupView(s.setup);
    case 'starting': return startingView(s.harness);
    case 'failed': return failedView(s.harness);
    default: return { heading: 'Opening OpenZenBot', body: '', steps: [], actions: [] };
  }
}
