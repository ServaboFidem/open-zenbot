// The bot screen's live activity feed: raw ledger events in, readable rows out.
import test from 'node:test';
import assert from 'node:assert/strict';
import { activityFeed, toolLabel } from '../../src/gui/actions.js';

const ev = (kind, data, ts = '2026-11-07T12:00:01+00:00', id = Math.random().toString(36).slice(2)) => ({ id, ts, kind, bot_id: 'b', session_id: 's', data });

test('a tool that has started but not finished is shown as running', () => {
  const rows = activityFeed([ev('tool_start', { tool: 'gpu-node.bing_search_to_markdown', arguments: { query: 'uv package manager' }, step: 1 })]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'running');
  assert.equal(rows[0].icon, 'search');
  assert.equal(rows[0].title, 'Web search');
  assert.equal(rows[0].detail, 'uv package manager');
  assert.equal(rows[0].meta, 'gpu-node');
});

test('the finished call settles the running row instead of adding another', () => {
  const rows = activityFeed([
    ev('tool_start', { tool: 'shell', arguments: { command: 'ls' }, step: 1 }),
    ev('tool_call', { tool: 'shell', arguments: { command: 'ls' }, ok: true, error: null, output: 'a.txt\nb.txt' }),
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'ok');
  assert.equal(rows[0].output, 'a.txt\nb.txt');
  assert.equal(rows[0].detail, 'ls');
});

test('a failed call shows its error', () => {
  const rows = activityFeed([
    ev('tool_start', { tool: 'x.crawl4ai_crawl', arguments: { url: 'https://example.com' } }),
    ev('tool_call', { tool: 'x.crawl4ai_crawl', arguments: { url: 'https://example.com' }, ok: false, error: 'timeout', output: '' }),
  ]);
  assert.equal(rows[0].status, 'error');
  assert.equal(rows[0].error, 'timeout');
  assert.equal(rows[0].title, 'Reading a page');
});

test('a tool call from before tool_start existed still gets one finished row', () => {
  const rows = activityFeed([ev('tool_call', { tool: 'shell', arguments: { command: 'pwd' }, ok: true, output: '/tmp' })]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'ok');
});

test('turns read as thinking when they call tools and as the answer when they do not', () => {
  const rows = activityFeed([
    ev('turn', { step: 1, content: 'Let me search for that.', tool_calls: ['web.search'], prompt_tokens: 100, completion_tokens: 20 }),
    ev('turn', { step: 2, content: 'uv is a fast Python package manager.', tool_calls: [], prompt_tokens: 300, completion_tokens: 40 }),
  ]);
  assert.equal(rows[0].icon, 'think');
  assert.equal(rows[0].detail, 'Let me search for that.');
  assert.equal(rows[1].icon, 'reply');
  assert.equal(rows[1].title, 'Wrote the answer');
  assert.match(rows[1].meta, /340 tokens/);
});

test('session start lists the injected skills; routine supervisor checks stay quiet', () => {
  const rows = activityFeed([
    ev('session', { event: 'start', task: 'research uv', injected: ['research-brief'] }),
    ev('supervisor', { ok: true, action: 'continue', run_state: 'progressing' }),
    ev('supervisor', { ok: true, action: 'nudge', run_state: 'looping' }),
    ev('decider_score', { outcome: 'band' }),
    ev('end', { state: 'done' }),
  ]);
  assert.deepEqual(rows.map(r => r.title), ['Task started', 'Supervisor nudged']);
  assert.equal(rows[0].detail, 'research uv');
  assert.match(rows[0].meta, /research-brief/);
});

test('an approval request settles when it is decided', () => {
  const rows = activityFeed([
    ev('approval_request', { approval_id: 'a1', tool: 'shell', title: 'shell: git push' }),
    ev('approval_decision', { approval_id: 'a1', decision: 'approved' }),
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, 'ok');
  assert.equal(rows[0].title, 'Approved');
});

test('an undecided approval waits', () => {
  const rows = activityFeed([ev('approval_request', { approval_id: 'a1', tool: 'shell', title: 'shell: git push' })]);
  assert.equal(rows[0].status, 'waiting');
});

test('tool labels: search, page, browser, shell, learn, other', () => {
  assert.equal(toolLabel('srv.web_search', { q: 'x' }).icon, 'search');
  assert.equal(toolLabel('srv.webpage_to_markdown', { url: 'https://a.b' }).detail, 'https://a.b');
  assert.equal(toolLabel('browser', { action: 'navigate', url: 'https://a.b' }).title, 'Browser');
  assert.equal(toolLabel('shell', { command: 'echo hi' }).detail, 'echo hi');
  assert.equal(toolLabel('learn_page', { url: 'https://c.d' }).icon, 'learn');
  const other = toolLabel('srv.make_widget', { size: 3, color: 'red' });
  assert.equal(other.title, 'make_widget');
  assert.match(other.detail, /size: 3/);
});

test('a delegate call is labelled with the agent and its run adds rows under it', () => {
  const rows = activityFeed([
    ev('tool_start', { tool: 'delegate', arguments: { agent: 'claude-code', task: 'fix the bug' } }),
    ev('delegate', { event: 'start', agent: 'claude-code', folder: 'C:\work\app' }),
    ev('delegate', { event: 'file', tool: 'Write', path: 'src/a.py' }),
    ev('delegate', { event: 'file', tool: 'Edit', path: 'C:\elsewhere\b.py', outside: true }),
    ev('delegate', { event: 'end', ok: true, seconds: 42, changed: ['src/a.py'] }),
    ev('tool_call', { tool: 'delegate', arguments: { agent: 'claude-code' }, ok: true, output: 'done' }),
  ]);
  assert.equal(rows.length, 5);
  assert.equal(rows[0].title, 'Claude Code');
  assert.equal(rows[0].detail, 'fix the bug');
  assert.equal(rows[0].status, 'ok');
  assert.match(rows[1].title, /Claude Code started/);
  assert.equal(rows[1].detail, 'C:\work\app');
  assert.equal(rows[2].title, 'Write');
  assert.equal(rows[2].detail, 'src/a.py');
  assert.equal(rows[2].status, 'info');
  assert.equal(rows[3].status, 'waiting');
  assert.match(rows[3].meta, /outside the job folder/);
  assert.equal(rows[4].status, 'ok');
  assert.match(rows[4].title, /finished/);
  assert.match(rows[4].detail, /42 s/);
  assert.match(rows[4].detail, /1 file changed/);
});

test('a failed delegate run closes as an error and codex is named', () => {
  const rows = activityFeed([
    ev('tool_start', { tool: 'delegate', arguments: { agent: 'codex', task: 't' } }),
    ev('delegate', { event: 'start', agent: 'codex', folder: '/w' }),
    ev('delegate', { event: 'end', ok: false, seconds: 3, changed: [] }),
  ]);
  assert.equal(rows[0].title, 'Codex');
  assert.equal(rows[2].status, 'error');
  assert.match(rows[2].title, /Codex stopped/);
});
