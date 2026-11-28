import React, { useEffect, useRef, useState } from 'react';
import { Button, Field, Input } from '@fluentui/react-components';
import { anchorId } from './settings-index.js';

const deliveryLabel = {
  disabled: 'Daily delivery is off.', waiting: 'Waiting for the next delivery.',
  sent: 'Today’s email was sent.', uncertain: 'Delivery is uncertain. ZenBot is checking for confirmation.',
  retry: 'A delivery retry is pending.', failed: 'The last delivery failed.',
  expired: 'Today’s delivery window has closed.', unavailable: 'Delivery status unavailable.',
};

function EmailSettings({ bridge, refreshStatus, notify }) {
  const [form, setForm] = useState(null);
  const [error, setError] = useState(''), [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    bridge?.get().then(value => { if (active) setForm(value); }).catch(() => {
      if (active) setError('The email settings could not be read.');
    });
    return () => { active = false; };
  }, [bridge]);
  async function save(e) {
    e.preventDefault();
    setSaving(true); setError('');
    try {
      const saved = await bridge.save(form);
      setForm(saved); notify(saved.recipient ? 'Morning email settings saved' : 'Daily email turned off');
      refreshStatus();
    } catch (e) { setError(e.message || 'The email settings could not be saved.'); }
    finally { setSaving(false); }
  }
  if (!bridge) return <p>Manage the daily email recipient and delivery time in the desktop app.</p>;
  return <form onSubmit={save} className="editor-form">
    <Field label="Digest recipient" hint="Saving an address enables the daily email from your connected AgentMail inbox. Clear the address and save to stop delivery.">
      <Input type="email" value={form?.recipient || ''} disabled={!form || saving}
        onChange={(_, d) => setForm({ ...form, recipient: d.value })}/>
    </Field>
    <Field label="Delivery time" hint="Local time. If ZenBot starts later in the morning, it can still send until noon.">
      <Input type="time" min="00:00" max="11:59" required value={form?.time || '07:00'} disabled={!form || saving}
        onChange={(_, d) => setForm({ ...form, time: d.value })}/>
    </Field>
    {error && <p className="form-error" role="alert">{error}</p>}
    <Button type="submit" appearance="outline" disabled={!form || saving}>Save email settings</Button>
  </form>;
}

// Settings > Notifications > Morning digest: two sections, Weather (the city and unit every morning edition uses: the
// digest in chat, the daily email and the spoken briefing) and Daily email (desktop app only). One component, since a
// weather save answers the email's delivery status too.
export function MorningSettings({ api, online, notify }) {
  const [weather, setWeather] = useState(null), [delivery, setDelivery] = useState('');
  const [query, setQuery] = useState(''), [choices, setChoices] = useState(null);
  const [busy, setBusy] = useState(''), [error, setError] = useState('');
  const search = useRef(0);
  useEffect(() => {
    let active = true;
    if (online) api.morning.settings().then(value => {
      if (!active) return;
      setWeather({ location: value.location, unit: value.unit });
      setQuery(value.location?.name || value.suggested_city || '');
      setDelivery(value.delivery?.status); setError('');
    }).catch(() => { if (active) setError('Morning settings unavailable.'); });
    return () => { active = false; search.current++; };
  }, [api, online]);

  async function find(e) {
    e.preventDefault();
    const ticket = ++search.current;
    setBusy('find'); setError(''); setChoices(null);
    try {
      const result = await api.morning.locations(query.trim());
      if (search.current === ticket) setChoices(result.locations);
    } catch (e) { if (search.current === ticket) setError(e.message); }
    finally { if (search.current === ticket) setBusy(''); }
  }
  async function saveWeather() {
    setBusy('save'); setError('');
    try {
      const saved = await api.morning.saveWeather(weather);
      setWeather({ location: saved.location, unit: saved.unit });
      setDelivery(saved.delivery?.status); notify('Weather settings saved');
    } catch (e) { setError(e.message); }
    finally { setBusy(''); }
  }
  const locked = !online || !weather || !!busy;
  const choose = location => {
    setWeather({ ...weather, location }); setQuery(location?.name || ''); setChoices(null);
  };
  const refreshStatus = () => api.morning.settings().then(value => setDelivery(value.delivery?.status))
    .catch(() => setDelivery('unavailable'));
  return <>
  <section className="resource-row editor-form morning-settings" id={anchorId('notifications', 'Weather')}>
    <h3>Weather</h3>
    <p>The morning digest in chat, the daily email and the spoken briefing all use this city.</p>
    <form className="editor-form" onSubmit={find}>
      <Field label="Weather city" hint="Search for a city, choose a match, then save your weather settings.">
        <Input value={query} disabled={!online || !weather || busy === 'save'} onChange={(_, d) => {
          search.current++; setQuery(d.value); setChoices(null); setBusy('');
        }}/>
      </Field>
      <Button type="submit" disabled={locked || !query.trim()}>Find city</Button>
      {choices && (choices.length ? <ul className="morning-locations" aria-label="Matching locations">
        {choices.map(place => <li key={`${place.latitude},${place.longitude}`}><button type="button"
          className="text-link" onClick={() => choose(place)}>{place.name}</button></li>)}
      </ul> : <p role="status">No cities found.</p>)}
    </form>
    <div>
      <p>{weather?.location ? `Forecast location · ${weather.location.name}` : 'Choose a city to include a forecast from Open-Meteo.'}</p>
      {weather?.location && <button className="text-link" disabled={locked} onClick={() => choose(null)}>Remove location</button>}
    </div>
    <Field label="Temperature unit"><select disabled={locked} value={weather?.unit || 'F'}
      onChange={e => setWeather({ ...weather, unit: e.target.value })}>
      <option value="F">Fahrenheit (°F)</option><option value="C">Celsius (°C)</option>
    </select></Field>
    {error && <p role="alert" className="form-error">{error}</p>}
    <Button appearance="outline" disabled={locked} onClick={saveWeather}>Save weather settings</Button>
  </section>
  <section className="resource-row editor-form morning-settings" id={anchorId('notifications', 'Daily email')}>
    <h3>Daily email</h3>
    <p>The email includes your agenda and up to five local recommendations when event discovery is on. Connect AgentMail and choose an inbox in Settings → Connected apps.</p>
    {delivery && <p role="status">{deliveryLabel[delivery] || deliveryLabel.unavailable}</p>}
    <EmailSettings bridge={window.openzenbot?.morningEmail} refreshStatus={refreshStatus} notify={notify}/>
  </section>
  </>;
}
