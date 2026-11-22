import React, { useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuList, MenuPopover, MenuTrigger } from '@fluentui/react-components';
import { ArrowDownload20Regular } from '@fluentui/react-icons';

// Export on the version shown. HTML is the numbered copy as it is; ZIP (newest version only) is the working file and
// its pictures. In the desktop app both go through the Save dialog (saveMedia), in a browser they download. PDF and
// PNG need the desktop app: the page sends the two ids and the main process renders the page (export-design.cjs).
// A result or a refusal shows as a sentence under the menu, never a dialog.
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

export default function ExportMenu({ api, convId, shown, onNewest, disabled }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const bridge = typeof window !== 'undefined' ? window.openzenbot : null;
  const title = String(shown.title || '').trim() || 'design';

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
  const render = kind => run(() => bridge.exportDesign(convId, shown.attId, kind, title));

  return <>
    <Menu>
      <MenuTrigger disableButtonEnhancement>
        <MenuButton appearance="subtle" icon={<ArrowDownload20Regular/>} disabled={busy} title={`Export Version ${shown.version}`}>Export</MenuButton>
      </MenuTrigger>
      <MenuPopover>
        <MenuList>
          <MenuItem onClick={() => save(api.conversationAttachmentUrl(convId, shown.attId), `${title}.html`)}>HTML</MenuItem>
          {onNewest && <MenuItem disabled={disabled} title={disabled ? 'Wait for the task to finish.' : undefined}
            onClick={() => save(api.designZipUrl(convId), `${title}.zip`)}>ZIP (page and pictures)</MenuItem>}
          {bridge?.exportDesign && <MenuItem onClick={() => render('pdf')}>PDF</MenuItem>}
          {bridge?.exportDesign && <MenuItem onClick={() => render('png')}>PNG</MenuItem>}
        </MenuList>
      </MenuPopover>
    </Menu>
    {(busy || note) && <p className="design-export-note" role="status">{busy ? 'Exporting…' : note}</p>}
  </>;
}
