// A bot's reply, rendered. react-markdown builds React elements and never injects raw HTML, so a
// reply cannot smuggle script or markup in; its default URL filter drops javascript: and the like.
// Media in the reply (images, video, PDFs, with their credits) and files the bot attached show as
// preview cards under the text, and leave the text so nothing shows twice.
// Each code block (where a bot hands over a prompt to paste elsewhere) has Copy under it, and the
// reply has Copy reply, so nothing needs selecting by hand.
import React, { useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '@fluentui/react-components';
import { Checkmark20Regular, Copy20Regular } from '@fluentui/react-icons';
import { codeText, copyText, isProse } from './copy.js';
import { mediaItems, remarkMediaCards } from './media.js';
import { MediaGrid } from './MediaCards';

/** Copy, then "Copied" (or "Couldn't copy") for a moment. */
export function CopyButton({ text, label = 'Copy', title }) {
  const [state, setState] = useState('idle');
  useEffect(() => {
    if (state === 'idle') return undefined;
    const t = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(t);
  }, [state]);
  return <Button size="small" appearance="subtle" title={title} icon={state === 'copied' ? <Checkmark20Regular/> : <Copy20Regular/>}
    onClick={async () => setState((await copyText(text)) ? 'copied' : 'failed')}>
    {state === 'copied' ? 'Copied' : state === 'failed' ? "Couldn't copy" : label}
  </Button>;
}

// Links leave the app: Electron's window-open handler hands http(s) URLs to the system browser.
const Link = ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
// An image left mid-sentence (its card is under the text) stays a link to where the picture lives.
const Image = ({ src, alt }) => (src ? <a href={src} target="_blank" rel="noopener noreferrer">{alt || 'image'}</a> : null);
// Wide tables scroll inside the message rather than pushing the chat sideways.
const Table = ({ children }) => <div className="md-table"><table>{children}</table></div>;

// A code block with Copy under it: the block's text exactly, without the reply around it.
const Code = ({ node, children }) => <div className={isProse(node) ? 'md-code prose' : 'md-code'}><pre>{children}</pre><div className="md-copy"><CopyButton text={codeText(node)} title="Copy this block"/></div></div>;

const COMPONENTS = { a: Link, img: Image, table: Table, pre: Code };
const PLUGINS = [remarkGfm, remarkMediaCards];

export default function Markdown({ text, attachments = [] }) {
  const fromText = useMemo(() => mediaItems(text || ''), [text]);
  const items = attachments.length ? [...fromText, ...attachments] : fromText;
  return <div className="markdown">
    {text ? <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>{text}</ReactMarkdown> : null}
    {items.length > 0 && <MediaGrid items={items}/>}
    {text?.trim() && <div className="md-reply-actions"><CopyButton text={text} label="Copy reply" title="Copy the whole reply"/></div>}
  </div>;
}
