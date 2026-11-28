import React from 'react';
import { Tab, TabList } from '@fluentui/react-components';

// Settings > Notifications: what ZenBot sends you and when. Spoken briefing (on the Sonos speakers), Morning digest (the
// weather city every morning edition uses, and the daily email) and Text reminders (where texts go and when; the Telnyx
// account itself is on Connected apps). tab is 'briefing', 'digest' or 'texts' and is held by the app, so the Settings
// search and links can open the right one (`tabOf`); briefing, digest and texts are the tabs' content.
export function NotificationsPage({ tab, onTab, briefing, digest, texts }) {
  return <>
    <TabList selectedValue={tab} className="skills-tabs" aria-label="Notifications sections" onTabSelect={(_, d) => onTab(d.value)}>
      <Tab value="briefing">Spoken briefing</Tab>
      <Tab value="digest">Morning digest</Tab>
      <Tab value="texts">Text reminders</Tab>
    </TabList>
    {tab === 'digest' ? digest : tab === 'texts' ? texts : briefing}
  </>;
}
