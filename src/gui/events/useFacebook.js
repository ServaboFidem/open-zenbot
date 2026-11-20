import { useCallback, useEffect, useRef, useState } from 'react';
import { EMPTY, LISTS_FAILED, POLL_MS, helperStatus, shapeLists, withChange } from './facebook.js';

// The Facebook tab's data: loads the lists when the tab opens, every POLL_MS while it stays open, and after a
// panel reports a change. Reads only, through api.discover.facebookLists; the panels do the writes.
// Returns the props FacebookPanels takes beside api, online and notify: invites, approvals, birthdays,
// status, now, onChanged.
export default function useFacebook(api, { online = true, active = false } = {}) {
  const discover = api?.discover;
  const [lists, setLists] = useState(EMPTY);
  const [raw, setRaw] = useState(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const mounted = useRef(true);
  const asked = useRef(0);   // only the newest load's answer is kept
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = useCallback(async () => {
    if (!discover?.facebookLists) return;
    const mine = ++asked.current;
    setLoading(true);
    try {
      const answer = await discover.facebookLists();
      if (!mounted.current || mine !== asked.current) return;
      setLists(shapeLists(answer)); setRaw(answer?.status || null); setFailed('');
    } catch (err) {
      if (mounted.current && mine === asked.current) setFailed(err?.message || LISTS_FAILED);
    } finally {
      if (mounted.current && mine === asked.current) { setLoading(false); setNow(Date.now()); }
    }
  }, [discover]);

  useEffect(() => {
    if (!active || !online) return undefined;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [active, online, load]);

  const onChanged = useCallback(item => { setLists(l => withChange(l, item)); load(); }, [load]);

  return { ...lists, status: helperStatus(raw, { loading, failed }), now, onChanged };
}
