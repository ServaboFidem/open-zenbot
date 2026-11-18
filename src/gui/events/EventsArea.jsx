import React, { useState } from 'react';
import { Tab, TabList } from '@fluentui/react-components';
import CalendarView from './CalendarView.jsx';
import DiscoverView from './DiscoverView.jsx';
import './events.css';

// The Events area: My Calendar (the calendar the harness keeps, with Google when connected) and Discover.
// props: api (createApi), online, notify(text), openSettings() opens the Calendar settings panel
export default function EventsArea({ api, online, notify, openSettings }) {
  const [tab, setTab] = useState('calendar');
  return <div className="events-area">
    <section className="page-heading od-row-top"><div className="od-fill"><h1>Events</h1></div></section>
    <TabList selectedValue={tab} className="events-tabs" aria-label="Events sections" onTabSelect={(_, d) => setTab(d.value)}>
      <Tab value="calendar">My Calendar</Tab>
      <Tab value="discover">Discover</Tab>
    </TabList>
    {tab === 'calendar'
      ? <CalendarView api={api} online={online} notify={notify} openSettings={openSettings}/>
      : <DiscoverView api={api} online={online} notify={notify} openSettings={openSettings}/>}
  </div>;
}
