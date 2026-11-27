import React from 'react';
import { Badge, Button, Field, Input, Switch } from '@fluentui/react-components';
import { toolAnchor } from './settings-index';
import { choiceKey, enabledByKind, modelLabel, parseChoice } from './models.js';
import { ReadAloudLocal } from './ReadAloudLocal';
import { LOCAL, LOCAL_LABEL, LOCAL_MODEL, showsNoSpeakerAlert, speechDirty, speechForm, speechPatch } from './speech-settings.js';

// The form a harness tool's card starts from, and what a save of it sends.
export const toolForm = t => (t.kind === 'tts' ? speechForm(t) : { choice: choiceKey(t.provider, t.model), voice: t.voice });
export const toolPatch = (t, f) => (t.kind === 'tts' ? speechPatch(t, f) : (f.choice !== choiceKey(t.provider, t.model) ? parseChoice(f.choice) : {}));

// Settings > Models > Jobs: one harness tool's card (Image generation, Read aloud): its switch, its model and, for
// speech, its voice or the voice made on this computer. The app holds the saved tools and the forms: t is the saved
// tool, form its form (toolForm(t) when untouched), setForm(next) replaces it, save(patch, done) saves it (done(tool)
// is the notice), onLocalStatus(tool) takes a newer tool from the local voice's download, openProviders opens
// Settings > Models > Providers.
export function ToolCard({ api, online, providers, t, form: f, setForm, save, onLocalStatus, openProviders, notify }) {
  const tts = t.kind === 'tts';
  const groups = enabledByKind(providers, tts ? 'tts' : 'image', t.providers);
  const cur = parseChoice(f.choice);
  const kok = tts && !!cur && cur.provider === LOCAL;
  const missing = !kok && cur && !groups.some(g => g.provider === cur.provider && g.models.some(m => m.id === cur.model));
  const dirty = tts ? speechDirty(t, f) : f.choice !== choiceKey(t.provider, t.model);
  return <form id={toolAnchor(t.id)} className="resource-row editor-form" onSubmit={e => { e.preventDefault(); save(toolPatch(t, f), x => `Saved ${x.name}: ${x.model}${x.voice ? `, voice ${x.voice}` : ''}`); }}>
    <div className="od-row-top tool-heading">
      <div className="od-field od-fill"><h3>{t.name}</h3><p>{t.summary}</p></div>
      <Switch checked={t.enabled} disabled={!online} label={t.enabled ? 'On' : 'Off'} onChange={(e, d) => save({ enabled: d.checked }, x => `${x.name} ${x.enabled ? 'on' : 'off'}`)}/>
    </div>
    {(tts ? showsNoSpeakerAlert(t) : t.enabled && !t.active) && <p className="form-error" role="alert">{tts ? `${t.provider} is not a provider that speaks, so replies cannot be read aloud. Pick a model below.` : `${t.provider} is not a provider that makes images, so bots are not offered this tool. Pick a model below.`}</p>}
    <Field label={tts ? 'Speech model' : 'Image model'} hint={tts && kok ? undefined : tts ? 'Choose from the speech models enabled on the Providers tab. Reading aloud runs through OpenRouter providers.' : 'Choose from the image models enabled on the Providers tab. Image generation runs through OpenRouter providers.'}>
      {groups.length || tts ? <select aria-label={tts ? 'Speech model' : 'Image model'} value={f.choice} disabled={!online} onChange={e => setForm({ ...f, choice: e.target.value })}>
        {missing && <option value={f.choice}>{cur.model} (not enabled)</option>}
        {tts && <optgroup label="On this computer"><option value={choiceKey(LOCAL, LOCAL_MODEL)}>{LOCAL_LABEL}</option></optgroup>}
        {groups.map(g => <optgroup key={g.provider} label={g.name}>{g.models.map(m => <option key={m.id} value={choiceKey(g.provider, m.id)}>{modelLabel(m)}</option>)}</optgroup>)}
      </select> : <p className="field-hint">{tts ? `Using ${t.model}. No speech models are enabled on an OpenRouter provider to choose from.` : 'No image models enabled on an OpenRouter provider.'} <button type="button" className="text-link" onClick={openProviders}>Enable one on the Providers tab</button>.</p>}
    </Field>
    {kok && <ReadAloudLocal api={api} tool={t} voice={f.localVoice} online={online} onVoice={v => setForm({ ...f, localVoice: v })} onStatus={onLocalStatus} notify={notify}/>}
    {tts && !kok && <Field label="Voice" hint="A voice the speech model knows, such as Zephyr, Puck, Kore or Charon."><Input aria-label="Voice" value={typeof f.voice === 'string' ? f.voice : t.voice} disabled={!online} onChange={(e, v) => setForm({ ...f, voice: v.value })}/></Field>}
    {!kok && <p className="field-hint">{tts ? "Uses the key of the model's provider. The Narrator bot rewrites each reply for the ear first, on its own model (Edit bot) or the chat model." : "Uses the key of the model's provider. Images are saved in the workspace's generated-images folder and attached to the bot's reply. Saved here, this choice wins over .env."}</p>}
    <div className="od-row">
      <Badge appearance="tint" color={t.active ? 'success' : 'informative'}>{tts ? (t.active ? 'On · the Read aloud button in each chat reads new replies' : 'Off · replies are not read aloud') : t.active ? `${t.tool} · offered when the decider picks it` : 'Off · no bot is offered it'}</Badge>
      <div className="od-fill"/>
      {dirty && <Button appearance="subtle" onClick={() => setForm(toolForm(t))}>Discard</Button>}
      <Button appearance="primary" type="submit" disabled={!online || !dirty}>Save</Button>
    </div>
  </form>;
}
