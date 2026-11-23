import React, { useEffect, useState } from 'react';
import { CalendarLtr24Regular } from '@fluentui/react-icons';
import { watchToday } from './morning.js';
import './morning.css';

function EventList({ events }) {
  return <ul className="today-events">{events.map((event, index) =>
    <li key={index}><span>{event.time || (event.all_day ? 'All day' : '')}</span><strong>{event.title}</strong></li>)}</ul>;
}

export function TodayCard({ morning, openSettings }) {
  const [data, setData] = useState({});
  useEffect(() => watchToday(morning, (source, value) =>
    setData(now => ({ ...now, [source]: value }))), [morning]);
  const { agenda, forecast } = data;
  const date = agenda?.date || forecast?.date;
  const dateLabel = (date ? new Date(`${date}T12:00:00`) : new Date())
    .toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const events = agenda?.events || [], warnings = agenda?.warnings || [];
  return <section className="today-card" aria-labelledby="today-title">
    <header><CalendarLtr24Regular aria-hidden="true"/><h2 id="today-title">Today</h2><time dateTime={date}>{dateLabel}</time></header>
    <div className="today-body">
      {forecast?.status === 'ready' && <p className="today-weather">
        <strong>{forecast.location}</strong> {forecast.summary} <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo</a>
      </p>}
      {forecast?.status === 'unconfigured' && <button className="text-link" type="button" onClick={openSettings}>Choose a weather location</button>}
      {forecast?.status === 'unavailable' && <p>Weather forecast unavailable.</p>}
      {!forecast && <p>Loading forecast…</p>}
      {!!agenda?.birthdays?.length && <div><h3>Birthdays</h3><p>{agenda.birthdays.join(', ')}</p></div>}
      <div><h3>On your calendar</h3>
        {!agenda ? <p>Loading agenda…</p> : agenda.status === 'unavailable' ? <p>Calendar and birthdays unavailable.</p> :
          events.length ? <><EventList events={events.slice(0, 3)}/>{events.length > 3 && <details>
            <summary>Show {events.length - 3} more {events.length === 4 ? 'event' : 'events'}</summary>
            <EventList events={events.slice(3)}/>
          </details>}</> : !warnings.includes('Calendar unavailable.') && <p>Nothing on your calendar today.</p>}
        {warnings.map(warning => <p key={warning}>{warning}</p>)}
      </div>
    </div>
  </section>;
}
