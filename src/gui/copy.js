// Copy buttons in a reply: under a code block (a prompt to paste elsewhere) and under the reply.
// Pure helpers, so they test without a DOM.

const textOf = node => (node.type === 'text' ? node.value : (node.children || []).map(textOf).join(''));

/** A rendered code block's text (the `pre` node react-markdown hands its override), as the user sees it. */
export const codeText = pre => textOf(pre).replace(/\n$/, '');

const PROSE = new Set(['text', 'plaintext', 'txt', 'prompt', 'markdown', 'md']);

/** A block with no language, or a plain-text one (a prompt, a message to paste), wraps its lines;
 *  real code keeps them and scrolls. */
export function isProse(pre) {
  const code = (pre.children || []).find(c => c.type === 'element' && c.tagName === 'code');
  const lang = (code?.properties?.className || []).map(String).find(c => c.startsWith('language-'));
  return !lang || PROSE.has(lang.slice('language-'.length).toLowerCase());
}

/** Write `text` to the clipboard. Returns whether it worked; never throws. */
export async function copyText(text, clipboard = globalThis.navigator?.clipboard) {
  if (!clipboard?.writeText) return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
