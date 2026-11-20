import React, { useState } from 'react';
import { Tab, TabList } from '@fluentui/react-components';
import CalendarView from './CalendarView.jsx';
import DiscoverView from './DiscoverView.jsx';
import AllEventsView from './AllEventsView.jsx';
import FacebookPanels from './FacebookPanels.jsx';
import './events.css';

// The Events area: My Calendar (the calendar the harness keeps, with Google when connected), Your
// Recommendations (DiscoverView: the scout's digests), All Events (every event read, as a grid) and
// Facebook (FacebookPanels: invites, approvals and birthdays from the browser helper).
// props: api (createApi), online, notify(text), openSettings() opens the Calendar settings panel
export default function EventsArea({ api, online, notify, openSettings }) {
  const [tab, setTab] = useState('calendar');
  const props = { api, online, notify, openSettings };
  return <div className="events-area">
    <section className="page-heading od-row-top"><div className="od-fill"><h1>Events</h1></div></section>
    <TabList selectedValue={tab} className="events-tabs" aria-label="Events sections" onTabSelect={(_, d) => setTab(d.value)}>
      <Tab value="calendar">My Calendar</Tab>
      <Tab value="discover">Your Recommendations</Tab>
      <Tab value="all">All Events</Tab>
      <Tab value="facebook">Facebook</Tab>
    </TabList>
    {tab === 'calendar' && <CalendarView {...props}/>}
    {tab === 'discover' && <DiscoverView {...props}/>}
    {tab === 'all' && <AllEventsView {...props}/>}
    {tab === 'facebook' && <FacebookPanels api={api} online={online} notify={notify}/>}
  </div>;
}
