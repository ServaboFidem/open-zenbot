import React, { useEffect, useState } from 'react';
import { Badge, Button, Field, Input, Spinner } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { langfuseBody, langfuseDirty, langfuseForm, secretHint, sentryDirty, sentryForm, sourceLabel } from './tracing.js';

// Settings > Tracing: the Sentry (GlitchTip) DSN and Langfuse's keys, server and environment. Saved in the
// vault-settings/tracing.json beside the vault (owner-only), over .env; the harness applies a save at once, the desktop app
// the DSN on its next launch. ctx: { api, online, guard, notify }
export function TracingSettingsTab({ ctx }) {
  const { api, online, guard, notify } = ctx;
  const [s, setS] = useState(null);
  const [sentry, setSentry] = useState(sentryForm(null));
  const [lf, setLf] = useState(langfuseForm(null));
  const [busy, setBusy] = useState('');
  const [error, setError] = useState({ sentry: '', langfuse: '' });

  const take = r => { setS(r); setSentry(sentryForm(r)); setLf(langfuseForm(r)); };
  useEffect(() => { if (online) guard(() => api.tracingSettings(), { quiet: true }).then(r => r && take(r)); }, [online]);

  async function run(which, fn, done) {
    setBusy(which); setError(e => ({ ...e, [which]: '' }));
    try { const r = await fn(); take(r); notify(done); }
    catch (e) { setError(x => ({ ...x, [which]: e?.message || 'Something went wrong.' })); }
    finally { setBusy(''); }
  }

  if (!online) return <div className="resource-row"><p>The harness is offline.</p></div>;
  if (!s) return <div className="resource-row"><Spinner size="tiny" label="Loading tracing settings…"/></div>;

  const badge = sec => <Badge appearance="tint" color={sec.live ? 'success' : 'informative'}>{sec.live ? 'On' : 'Off'} · {sourceLabel(sec)}</Badge>;

  return <>
    <div className="resource-row"><h3 id={anchorId('tracing', 'Tracing')}>Tracing</h3><p>Where the harness sends errors and traces. Saved here, a setting wins over .env and is kept in vault-settings/tracing.json beside your vault, readable by you only and out of your bots' reach. Clear a section to go back to .env.</p></div>

    <form className="resource-row editor-form" onSubmit={e => { e.preventDefault(); run('sentry', () => api.setSentrySettings(sentry.dsn.trim()), sentry.dsn.trim() ? 'Sentry DSN saved' : 'Sentry turned off'); }}>
      <div className="od-row-top tool-heading"><div className="od-field od-fill"><h3 id={anchorId('tracing', 'Sentry (GlitchTip)')}>Sentry (GlitchTip)</h3><p>Errors and request traces from the harness and the desktop app.</p></div>{badge(s.sentry)}</div>
      <Field label="DSN" hint="From your GlitchTip or Sentry project's settings: https://<key>@<host>/<project id>. Leave empty and save to turn Sentry off." validationMessage={error.sentry || undefined}>
        <Input value={sentry.dsn} placeholder="https://…@glitchtip.example/1" onChange={(e, d) => setSentry({ dsn: d.value })} autoComplete="off" spellCheck={false}/>
      </Field>
      <p className="field-hint">The harness uses a new DSN right away. The desktop app picks it up the next time you open it.</p>
      <div className="od-row"><div className="od-fill"/>
        {s.sentry.source === 'saved' && <Button appearance="subtle" disabled={!!busy} onClick={() => run('sentry', () => api.clearSentrySettings(), 'Sentry back to .env')}>Clear</Button>}
        {sentryDirty(s, sentry) && <Button appearance="subtle" onClick={() => setSentry(sentryForm(s))}>Discard</Button>}
        <Button appearance="primary" type="submit" disabled={!!busy || !sentryDirty(s, sentry)}>{busy === 'sentry' ? 'Saving…' : 'Save'}</Button>
      </div>
    </form>

    <form className="resource-row editor-form" onSubmit={e => { e.preventDefault(); run('langfuse', () => api.setLangfuseSettings(langfuseBody(lf)), lf.public_key.trim() ? 'Langfuse settings saved' : 'Langfuse turned off'); }}>
      <div className="od-row-top tool-heading"><div className="od-field od-fill"><h3 id={anchorId('tracing', 'Langfuse')}>Langfuse</h3><p>Every agent run, model call and tool call, with thinking, tokens and cost.</p></div>{badge(s.langfuse)}</div>
      <Field label="Public key" hint="Settings › API Keys in your Langfuse project. Leave empty and save to turn Langfuse off.">
        <Input value={lf.public_key} placeholder="pk-lf-…" onChange={(e, d) => setLf(f => ({ ...f, public_key: d.value }))} autoComplete="off" spellCheck={false}/>
      </Field>
      <Field label="Secret key" hint={`${secretHint(s.langfuse)}. Leave empty to keep it.`}>
        <Input type="password" value={lf.secret_key} placeholder="sk-lf-…" onChange={(e, d) => setLf(f => ({ ...f, secret_key: d.value }))} autoComplete="new-password"/>
      </Field>
      <Field label="Server" hint="Your Langfuse URL. Empty means Langfuse Cloud (EU).">
        <Input value={lf.base_url} placeholder="https://cloud.langfuse.com" onChange={(e, d) => setLf(f => ({ ...f, base_url: d.value }))} autoComplete="off" spellCheck={false}/>
      </Field>
      <Field label="Environment" hint="Keeps test traces apart from real ones, e.g. production or development. Empty is Langfuse's default." validationMessage={error.langfuse || undefined}>
        <Input value={lf.environment} placeholder="production" onChange={(e, d) => setLf(f => ({ ...f, environment: d.value }))} autoComplete="off" spellCheck={false}/>
      </Field>
      <p className="field-hint">Saving checks the keys with the server first and applies them right away.</p>
      <div className="od-row"><div className="od-fill"/>
        {s.langfuse.source === 'saved' && <Button appearance="subtle" disabled={!!busy} onClick={() => run('langfuse', () => api.clearLangfuseSettings(), 'Langfuse back to .env')}>Clear</Button>}
        {langfuseDirty(s, lf) && <Button appearance="subtle" onClick={() => setLf(langfuseForm(s))}>Discard</Button>}
        <Button appearance="primary" type="submit" disabled={!!busy || !langfuseDirty(s, lf)}>{busy === 'langfuse' ? 'Checking…' : 'Save'}</Button>
      </div>
    </form>
  </>;
}
