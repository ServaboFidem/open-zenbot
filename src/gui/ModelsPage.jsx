import React from 'react';
import { Tab, TabList } from '@fluentui/react-components';

// Settings > Models: Jobs (which model each job runs on: the harness's roles, image generation, reading aloud) and
// Providers (where models come from: providers, their keys, which models are enabled). tab is 'jobs' or 'providers' and
// is held by the app, so the Settings search and the "enable one" links can open Providers; jobs and providers are the
// two tabs' content.
export function ModelsPage({ tab, onTab, jobs, providers }) {
  return <>
    <TabList selectedValue={tab} className="skills-tabs" aria-label="Models sections" onTabSelect={(_, d) => onTab(d.value)}>
      <Tab value="jobs">Jobs</Tab>
      <Tab value="providers">Providers</Tab>
    </TabList>
    {tab === 'providers' ? providers : jobs}
  </>;
}
