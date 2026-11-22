import React, { useState } from 'react';
import { Dropdown, Option } from '@fluentui/react-components';
import { compareDefaults } from './design-edits.js';
import { studioFrameUrl } from './design.js';

// Compare: two frames side by side, each with its own Version dropdown (left the previous version, right the newest
// at first). Both are the ordinary closed preview; the pane sends them nothing, so no pick, edit or tweak reaches them.
export default function DesignCompare({ revisions, urlFor, label }) {
  const [ids, setIds] = useState(() => compareDefaults(revisions));
  const side = (i, name) => {
    const r = revisions.find(x => x.attId === ids[i]) || revisions.at(-1);
    return <div className="design-compare-side od-stack" key={i}>
      <Dropdown className="design-version" aria-label={`${name} version`} value={label(r)} selectedOptions={[r.attId]} onOptionSelect={(e, d) => d.optionValue && setIds(now => now.map((x, j) => j === i ? d.optionValue : x))}>{[...revisions].reverse().map(x => <Option key={x.attId} value={x.attId} text={label(x)}>{label(x)}</Option>)}</Dropdown>
      <iframe key={r.attId} className="design-frame" sandbox="allow-scripts" src={studioFrameUrl(urlFor(r.attId))} title={`${name}: ${r.title}, version ${r.version}`}/>
    </div>;
  };
  return <div className="design-compare">{side(0, 'Left')}{side(1, 'Right')}</div>;
}
