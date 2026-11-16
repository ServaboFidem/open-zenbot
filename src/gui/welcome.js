// The first-launch OpenRouter key screen: when it shows and what a failed save says. Pure, so it is tested without the
// page (tests/gui/welcome.test.mjs). An older harness sends no `openrouter_key`, so no field means no screen.
export function showWelcome({ online, settings, dismissed }) {
  return !!online && !!settings && settings.openrouter_key === false && !dismissed;
}

// The harness words a refused key (422) and an unreachable OpenRouter (503) itself; anything else gets a plain line.
export function saveError(err) {
  if ((err?.status === 422 || err?.status === 503) && typeof err.message === 'string' && err.message) return err.message;
  return 'Something went wrong saving the key.';
}
