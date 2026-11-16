import React, { useState } from 'react';
import { Button, Input, Field } from '@fluentui/react-components';
import { saveError } from './welcome';

// The first-launch screen: why one OpenRouter key is needed, and where to enter it. The key lives only in this state
// until it is sent to the harness, and is cleared after a good save. `onSaved` refreshes the app's settings.
export function WelcomeKey({ api, onSaved, onSkip }) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      await api.saveOpenRouterKey(key);
      setKey('');
      await onSaved();
    } catch (err) { setError(saveError(err)); }
    finally { setBusy(false); }
  }
  return <div className="welcome-key" role="dialog" aria-modal="true" aria-labelledby="welcome-key-title">
    <form className="welcome-card" onSubmit={save}>
      <h1 id="welcome-key-title">One key to get started</h1>
      <p>OpenZenBot needs an OpenRouter account for one thing: the decider model (microsoft-decision-1). It picks the right skills and tools for each task and checks shell commands before they run, and the harness does not work properly without it.</p>
      <p>Everything else can run on this computer or through NVIDIA NIM: the models your bots talk with are your choice, in Settings.</p>
      <div><Button as="a" appearance="outline" href="https://openrouter.ai" target="_blank" rel="noreferrer">Create an OpenRouter account</Button></div>
      <Field label="OpenRouter API key" validationState={error ? 'error' : undefined} validationMessage={error || undefined}>
        <Input type="password" autoComplete="off" value={key} onChange={(_, d) => setKey(d.value)}/>
      </Field>
      <div className="welcome-actions">
        <Button appearance="primary" type="submit" disabled={busy || !key.trim()}>Save and continue</Button>
        <Button appearance="transparent" type="button" onClick={onSkip}>Not now</Button>
      </div>
    </form>
  </div>;
}
