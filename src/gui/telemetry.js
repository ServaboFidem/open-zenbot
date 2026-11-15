// Errors and traces to GlitchTip, through the Electron main process. On only when the
// preload set up Sentry's IPC bridge, which it does when ZEN_GLITCHTIP_DSN is set; the
// page itself never holds the DSN. Requests to the harness carry sentry-trace and baggage,
// so a GUI action and the harness work behind it are one trace.
import * as Sentry from '@sentry/electron/renderer';

export function startTelemetry({ win = typeof window === 'undefined' ? {} : window, harness, sentry = Sentry } = {}) {
  if (!win.__SENTRY_IPC__) return false;
  sentry.init({
    integrations: [sentry.browserTracingIntegration()],
    tracesSampleRate: 1.0,
    traceLifecycle: 'static',  // whole transactions: GlitchTip does not take streamed spans
    tracePropagationTargets: harness ? [harness] : [],
    sendDefaultPii: false,
  });
  return true;
}
