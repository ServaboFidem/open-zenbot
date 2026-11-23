import React, { useEffect, useRef, useState } from 'react';
import { Button, Spinner } from '@fluentui/react-components';
import { ArrowLeft24Regular, ArrowSync24Regular, Copy24Regular } from '@fluentui/react-icons';
import { copyText } from './copy.js';
import { errorLine, senderName, timeLabel } from './mail.js';
import './mail.css';

// The AgentMail page: ZenBot's address with Copy, and its inbox, read-only. Mail is written by strangers, so a
// message is shown as plain text only: never its HTML, never through Markdown, and links are not clickable. The
// inbox is read when the page opens and on Refresh, never on a timer.
// Props: { api, online, email }
export default function MailPage({ api, online, email }) {
  const mail = api.mail;
  const [page, setPage] = useState(null);  // { messages, next_page_token }
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState('');
  const [detail, setDetail] = useState(null);
  const [detailError, setDetailError] = useState('');
  const [copied, setCopied] = useState('');
  const copyTimer = useRef(null);
  const asked = useRef(0);  // the newest message asked for; a slower earlier answer is dropped

  async function load(token) {
    setLoading(true); setError('');
    try {
      const got = await mail.messages(token);
      setPage(prev => token && prev ? { messages: [...prev.messages, ...got.messages], next_page_token: got.next_page_token }
        : { messages: got.messages, next_page_token: got.next_page_token });
    } catch (e) { setError(errorLine(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { if (online) load(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => clearTimeout(copyTimer.current), []);

  async function open(id) {
    const n = ++asked.current;
    setOpenId(id); setDetail(null); setDetailError('');
    try { const got = await mail.message(id); if (n === asked.current) setDetail(got); }
    catch (e) { if (n === asked.current) setDetailError(errorLine(e)); }
  }
  function close() { asked.current++; setOpenId(''); setDetail(null); setDetailError(''); }
  function refresh() { close(); load(); }
  async function copy() {
    const ok = await copyText(email);
    setCopied(ok ? 'Copied' : 'Copy failed');
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(''), 2000);
  }

  const messages = page?.messages || [];
  return <div className="mail-page">
    <section className="page-heading"><div className="eyebrow">ZENBOT'S OWN EMAIL ADDRESS</div><h1>AgentMail</h1>
      <p>Read messages delivered to ZenBot’s address. To receive a daily morning digest, set a recipient in Settings → Calendar.</p></section>
    <div className="mail-head">
      <code className="mail-address" aria-label="ZenBot's email address">{email}</code>
      <Button appearance="outline" icon={<Copy24Regular/>} disabled={!email} onClick={copy}>{copied || 'Copy'}</Button>
      <span className="mail-spacer"/>
      <Button appearance="subtle" icon={<ArrowSync24Regular/>} disabled={!online || loading} onClick={refresh}>Refresh</Button>
    </div>
    {!online ? <p>Mail is read through the harness. Start it to see the inbox.</p>
      : error && !page ? <div className="od-row"><p role="alert" className="od-fill">{error}</p><Button appearance="outline" onClick={() => load()}>Try again</Button></div>
      : !page ? <Spinner size="tiny" label="Reading the inbox…"/>
      : messages.length === 0 ? <p>No mail yet. Give out the address above to start receiving.</p>
      : <div className={`mail-body ${openId ? '' : 'single'}`}>
          <div className="mail-list" hidden={!!openId && narrow()}>
            {messages.map(m => <button type="button" key={m.message_id} className={`mail-row ${m.message_id === openId ? 'selected' : ''}`}
              aria-current={m.message_id === openId ? 'true' : undefined} onClick={() => open(m.message_id)}>
              <span className="mail-from">{senderName(m.from) || '(Unknown sender)'}</span>
              <span className="mail-time">{timeLabel(m.timestamp)}</span>
              <span className="mail-subject">{m.subject || '(No subject)'}</span>
              {m.preview && <span className="mail-preview">{m.preview}</span>}
            </button>)}
            {page.next_page_token && <div className="mail-more"><Button appearance="subtle" disabled={loading} onClick={() => load(page.next_page_token)}>{loading ? 'Loading…' : 'Load more'}</Button></div>}
            {error && <p role="alert" className="mail-more">{error}</p>}
          </div>
          {openId && <article className="mail-detail">
            <div><Button appearance="subtle" icon={<ArrowLeft24Regular/>} onClick={close}>Back to the inbox</Button></div>
            {detailError ? <p role="alert">{detailError}</p>
              : !detail ? <Spinner size="tiny" label="Opening…"/>
              : <>
                  <h2>{detail.subject || '(No subject)'}</h2>
                  <dl className="mail-meta">
                    <dt>From</dt><dd>{detail.from}</dd>
                    <dt>To</dt><dd>{detail.to.join(', ')}</dd>
                    {detail.cc.length > 0 && <><dt>Cc</dt><dd>{detail.cc.join(', ')}</dd></>}
                    <dt>Date</dt><dd>{dateLine(detail.timestamp)}</dd>
                    {detail.attachments.length > 0 && <><dt>Attachments</dt><dd>{detail.attachments.join(', ')}</dd></>}
                  </dl>
                  <pre className="mail-text">{detail.text || '(This message has no text.)'}</pre>
                </>}
          </article>}
        </div>}
  </div>;
}

// Below 900 px the message takes the list's place, with Back to return.
const narrow = () => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 900px)').matches;
const dateLine = ts => { const d = new Date(ts || ''); return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(); };
