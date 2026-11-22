import React, { useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuList, MenuPopover, MenuTrigger } from '@fluentui/react-components';
import { ArrowDownload20Regular } from '@fluentui/react-icons';
import { exportItems } from './design-edits.js';

// Export on the version shown (which items show: design-edits.js exportItems). HTML is the numbered copy as it is;
// ZIP is the working file and its pictures. In the desktop app both go through the Save dialog (saveMedia), in a
// browser they download. PDF and PNG need the desktop app: the page sends the two ids and the main process renders the
// page (export-design.cjs). A result or a refusal shows as a sentence under the menu, never a dialog.
async function download(url, name) {
  const res = await fetch(url);
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' && detail ? detail : `The file could not be fetched (${res.status}).`);
  }
  const href = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement('a'), { href, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export default function ExportMenu({ api, convId, shown, isNewest, disabled }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const bridge = typeof window !== 'undefined' ? window.openzenbot : null;
  const items = exportItems({ isNewest, hasBridge: !!bridge?.exportDesign, disabled, title: shown.title });

  const run = async work => {
    if (busy) return;
    setBusy(true); setNote('');
    try {
      const r = await work();
      if (r?.saved) setNote(`Saved to ${r.saved}`);
      else if (r?.error) setNote(r.error);
    } catch (e) {
      setNote(e?.message || 'The design could not be exported.');
    } finally { setBusy(false); }
  };
  const save = (url, name) => run(() => (bridge?.saveMedia ? bridge.saveMedia(url, name) : download(url, name)));
  const start = item => {
    if (item.kind === 'html') save(api.conversationAttachmentUrl(convId, shown.attId), item.name);
    else if (item.kind === 'zip') save(api.designZipUrl(convId), item.name);
    else run(() => bridge.exportDesign(convId, shown.attId, item.kind, item.name));
  };

  return <>
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <MenuButton appearance="subtle" icon={<ArrowDownload20Regular/>} disabled={busy} title={`Export Version ${shown.version}`}>Export</MenuButton>
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          {items.map(item => <MenuItem key={item.kind} disabled={!!item.why} title={item.why} onClick={() => start(item)}>{item.label}</MenuItem>)}
        </MenuList>
      </MenuPopover>
    </Menu>
    {(busy || note) && <p className="design-export-note" role="status">{busy ? 'Exporting…' : note}</p>}
  </>;
}
