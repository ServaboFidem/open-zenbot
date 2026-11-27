import React from 'react';
import { Tab, TabList } from '@fluentui/react-components';
import { SkillTunerSection } from './SkillTunerSection.jsx';
import { SkillDiscovery } from './SkillDiscovery.jsx';
import { TunerSuggestions } from './TunerSuggestions.jsx';
import { tunerTabLabel } from './skilltuner.js';

// Settings > Skills: the catalog on Main, and everything SkillTuner does on its own tab, in this order: the switch,
// where it looks, and the suggestions waiting for the owner. tab is 'main' or 'tuner' and is held by the app, so the
// Overview line and the Settings search can open the SkillTuner tab; pending is the waiting count for the label;
// main is the Main tab's content; openDreaming opens Settings > Memory > Dreaming, where dreaming is switched on.
// The suggestion cards stay mounted (hidden) on Main: they also read which skills SkillTuner installed, which the
// catalog's Remove buttons need (onDiscovered).
export function SkillsPage({ tab, onTab, pending, main, ctx, bots, onDiscovered, onChanged, onDecided, openDreaming }) {
  const tuner = tab === 'tuner';
  return <>
    <TabList selectedValue={tab} className="skills-tabs" aria-label="Skills sections" onTabSelect={(_, d) => onTab(d.value)}>
      <Tab value="main">Main</Tab>
      <Tab value="tuner">{tunerTabLabel(pending)}</Tab>
    </TabList>
    {tuner && <><SkillTunerSection ctx={ctx} openDreaming={openDreaming}/><SkillDiscovery ctx={ctx}/></>}
    <div hidden={!tuner}><TunerSuggestions ctx={ctx} bots={bots} onDiscovered={onDiscovered} onChanged={onChanged} onDecided={onDecided}/></div>
    {!tuner && main}
  </>;
}
