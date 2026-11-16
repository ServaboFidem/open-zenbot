import React from 'react';
import { Field, Input } from '@fluentui/react-components';
import { Apps24Regular, BrainCircuit24Regular, Cloud24Regular, Code24Regular, Database24Regular, Folder24Regular, Info24Regular, Pulse24Regular, Search24Regular, Wrench24Regular } from '@fluentui/react-icons';
import { PANELS, searchSettings } from './settings-index';

export const PANEL_ICONS = {
  harness: <BrainCircuit24Regular/>, providers: <Cloud24Regular/>, tools: <Wrench24Regular/>, agents: <Code24Regular/>, folders: <Folder24Regular/>,
  tracing: <Pulse24Regular/>, memory: <Database24Regular/>, apps: <Apps24Regular/>, about: <Info24Regular/>,
};

// The Settings home screen: a tile per panel, and a search that lists the panels and sections holding the words typed.
// query, setQuery: what is typed, kept by the page so it is still there after a look at one result;
// extra: live sections for the search (settings-index.js); onOpen(panelId, anchor) opens a panel, at a section when anchor is set.
export function SettingsHome({ query, setQuery, extra, onOpen }) {
  const searching = !!query.trim();
  const results = searching ? searchSettings(query, extra) : [];
  return <>
    <div className="search-field settings-search"><Field label="Search settings">
      <Input type="search" contentBefore={<Search24Regular/>} value={query} placeholder="A setting, a key, a program…"
        onChange={(e, d) => setQuery(d.value)} onKeyDown={e => { if (e.key === 'Escape') setQuery(''); }}/>
    </Field></div>
    {!searching && <div className="settings-grid">{PANELS.map(p => <button key={p.id} type="button" className="settings-tile" onClick={() => onOpen(p.id, '')}>
      <span className="settings-tile-icon">{PANEL_ICONS[p.id]}</span><h3>{p.name}</h3><p>{p.description}</p>
    </button>)}</div>}
    {searching && <>
      <p className="field-hint settings-count" aria-live="polite">{results.length === 1 ? '1 match' : `${results.length} matches`}</p>
      <div className="settings-panel" role="list">
        {!results.length && <div className="resource-row"><p>No settings match “{query.trim()}”.</p></div>}
        {results.map((m, i) => <div role="listitem" key={`${m.panel}:${m.anchor}:${m.section}:${i}`}><button type="button" className="settings-result" onClick={() => onOpen(m.panel, m.anchor)}>
          <span className="settings-tile-icon">{PANEL_ICONS[m.panel]}</span>
          <span className="od-field od-fill"><strong>{m.section === m.panelName ? m.panelName : `${m.panelName} › ${m.section}`}</strong>{m.text !== m.section && <span>{m.text}</span>}</span>
        </button></div>)}
      </div>
    </>}
  </>;
}
