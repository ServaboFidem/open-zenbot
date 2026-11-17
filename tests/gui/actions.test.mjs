// Pure decisions the app makes before it calls the harness.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as actions from '../../src/gui/actions.js';
import { conversationLabel, latestWins, rememberPayload, showOnboarding, scanSummary, showNewTopicsOffer, extractionEstimate, rebuildSummary, extractionNote, pollExtraction, WAITING_FOR_NAME, warming, bootProgress } from '../../src/gui/actions.js';

test('followUpTask is gone', () => {
  assert.equal(actions.followUpTask, undefined);
});

test('latestWins lets only the newest of overlapping loads write', async () => {
  const begin = latestWins();
  const first = begin(), second = begin();
  assert.equal(first(), false);
  assert.equal(second(), true);
  const third = begin();
  assert.equal(second(), false);
  assert.equal(third(), true);
  const other = latestWins()();  // each guard counts on its own
  assert.equal(other(), true);
  assert.equal(third(), true);
});

test('conversationLabel shows title and date', () => {
  const label = conversationLabel({ id: 'c1', title: 'Pricing for the Q3 launch', updated_at: '2026-11-07T11:40:00Z' });
  assert.match(label, /^Pricing for the Q3 launch · /);
  assert.match(label, /Nov|11/);
  assert.match(conversationLabel({ id: 'c2', title: '', updated_at: '2026-11-07T11:40:00Z' }), /^New conversation · /);
  assert.equal(conversationLabel({ id: 'c3', title: '  ' }), 'New conversation');
});

test('recentConversationMeta names the bot, counts messages, and says when', () => {
  const now = new Date('2026-11-15T15:00:00Z');
  const meta = actions.recentConversationMeta({ owner: 'bot1', exchanges: 3, updated_at: '2026-11-07T11:40:00Z' }, 'Researcher', now);
  assert.equal(meta, 'Researcher · 3 messages · Nov 7');
  assert.match(actions.recentConversationMeta({ owner: 'bot1', exchanges: 1, updated_at: now.toISOString() }, 'Researcher', now), /^Researcher · 1 message · today /);
  assert.equal(actions.recentConversationMeta({ owner: 'gone-bot', exchanges: 2 }, undefined, now), 'gone-bot · 2 messages');
});

test('remember this goes to shared project memory with the session as source', () => {
  const p = rememberPayload({ id: 's1', result: 'uv is fast', task: 'what is uv' });
  assert.equal(p.namespace, 'shared'); assert.equal(p.tier, 'project'); assert.equal(p.text, 'uv is fast'); assert.equal(p.source, 'session:s1');
});

test('the onboarding card shows only for an empty graph with the assistant present and not dismissed', () => {
  const bots = [{ id: 'personal-assistant' }];
  assert.equal(showOnboarding({ facts: [], bots, dismissed: false }), true);
  assert.equal(showOnboarding({ facts: [{ id: 'f' }], bots, dismissed: false }), false);
  assert.equal(showOnboarding({ facts: [], bots: [{ id: 'other' }], dismissed: false }), false);
  assert.equal(showOnboarding({ facts: [], bots, dismissed: true }), false);
});

test('the new-topics offer shows once to an old install: a name fact, no family fact, not dismissed', () => {
  const old = [{ source: 'onboarding:name' }, { source: 'onboarding:job' }];
  assert.equal(showNewTopicsOffer({ facts: old, dismissed: false }), true);
  assert.equal(showNewTopicsOffer({ facts: old, dismissed: true }), false);
  assert.equal(showNewTopicsOffer({ facts: [...old, { source: 'onboarding:family' }], dismissed: false }), false);
  assert.equal(showNewTopicsOffer({ facts: [{ source: 'onboarding:job' }], dismissed: false }), false);
  assert.equal(showNewTopicsOffer({ facts: [], dismissed: false }), false);
});

test('scanSummary formats counts, size, and skips', () => {
  assert.equal(scanSummary({ dir: 'F:/notes', files: 1284, bytes: 312_000_000, skipped: { hidden: 38, no_converter: 2, too_large: 0 } }),
    'Import 1,284 files (312 MB) from F:/notes? 40 skipped (hidden, no converter). One summary and one memory write per file.');
  assert.equal(scanSummary({ dir: 'D:/x', files: 3, bytes: 412_000, skipped: { hidden: 0, no_converter: 0, too_large: 0 } }),
    'Import 3 files (412 KB) from D:/x? One summary and one memory write per file.');
  assert.equal(scanSummary({ dir: 'D:/x', files: 2, bytes: 3_200_000, skipped: { too_large: 1 } }),
    'Import 2 files (3.2 MB) from D:/x? 1 skipped (too large). One summary and one memory write per file.');
  assert.equal(scanSummary({ dir: 'D:/x', files: 0, bytes: 0, skipped: {} }), 'Nothing to import from D:/x.');
});

test('the overlay stays up while the harness warms, and opens on any end state', () => {
  assert.equal(warming(null), true);
  assert.equal(warming({ state: 'pending' }), true);
  assert.equal(warming({ state: 'warming' }), true);
  for (const state of ['ready', 'skipped', 'failed']) assert.equal(warming({ state }), false);
});

test('the loading bar moves within a step and never passes the next one', () => {
  assert.equal(bootProgress(null), 0);
  assert.equal(bootProgress({ state: 'ready' }), 1);
  const w = { state: 'warming', done: 1, total: 3, step_expected_s: 6 };
  assert.equal(bootProgress({ ...w, step_elapsed_s: 0 }), 1 / 3);
  assert.equal(bootProgress({ ...w, step_elapsed_s: 3 }), 1.5 / 3);
  assert.equal(bootProgress({ ...w, step_elapsed_s: 60 }), 1.9 / 3);  // a slow step holds at 90% of itself
});

test('extractionEstimate words the import confirmation', () => {
  assert.equal(extractionEstimate({ files: 1284, extraction_calls: 3050 }), '1,284 files · about 3,050 extraction calls, run in the background');
  assert.equal(extractionEstimate({ files: 3 }), '3 files · about 0 extraction calls, run in the background');
});

test('rebuildSummary words the rebuild confirmation', () => {
  assert.equal(rebuildSummary({ facts: 1284, extraction_calls: 3050 }),
    'Re-learn 1,284 shared facts · about 3,050 extraction calls, run in the background. The shared memory is cleared first; your facts and each bot\'s private memory stay.');
  assert.equal(rebuildSummary({ facts: 1, extraction_calls: 2 }),
    'Re-learn 1 shared fact · about 2 extraction calls, run in the background. The shared memory is cleared first; your facts and each bot\'s private memory stay.');
  assert.equal(rebuildSummary({ facts: 0, extraction_calls: 0 }), 'There are no shared facts to rebuild.');
});

test('extractionNote reports progress, the name wait, both, failures, or nothing', () => {
  assert.equal(extractionNote(null), null);
  assert.equal(extractionNote({ queued: 0, done: 4, failed: 0, waiting_for_owner: false }), null);
  assert.equal(extractionNote({ queued: 5, done: 2, failed: 0 }), 'Learning from your files in the background: 2 done, 5 queued');
  assert.equal(extractionNote({ queued: 0, waiting_for_owner: true }), WAITING_FOR_NAME);
  assert.equal(extractionNote({ queued: 5, done: 2, failed: 0, waiting_for_owner: true }),
    `Learning from your files in the background: 2 done, 5 queued. ${WAITING_FOR_NAME}`);
  assert.equal(extractionNote({ queued: 5, done: 2, failed: 1 }), 'Learning from your files in the background: 2 done, 5 queued, 1 failed');
});

test('pollExtraction runs only while jobs are moving', () => {
  assert.equal(pollExtraction({ queued: 3, waiting_for_owner: false }), true);
  assert.equal(pollExtraction({ queued: 3, waiting_for_owner: true }), false);
  assert.equal(pollExtraction({ queued: 0 }), false);
  assert.equal(pollExtraction(null), false);
});

test('emptyReplyNote explains a stopped or interrupted exchange with no reply', () => {
  const { emptyReplyNote } = actions;
  assert.equal(emptyReplyNote({ outcome: 'stopped', reply: '' }), 'Stopped before finishing.');
  assert.equal(emptyReplyNote({ outcome: 'interrupted', reply: '' }), 'Interrupted: the app closed before this finished.');
  assert.equal(emptyReplyNote({ outcome: 'stopped', reply: 'partial answer' }), '');  // the reply shows instead
  assert.equal(emptyReplyNote({ outcome: 'done', reply: '' }), '');
  assert.equal(emptyReplyNote({ outcome: 'failed' }), '');
});

test('designMeta gives the newest version, the count when there are several, and when', () => {
  const now = new Date('2026-11-16T12:00:00Z');
  assert.equal(actions.designMeta({ version: 1, versions: 1, updated_at: '2026-11-07T11:40:00Z' }, now), 'Version 1 · Nov 7');
  assert.match(actions.designMeta({ version: 3, versions: 3, updated_at: now.toISOString() }, now), /^Version 3 · 3 versions · today /);
  assert.equal(actions.designMeta({}, now), 'Version 1');
});

test('directSessions includes a helper turn in the bot\'s own conversation', () => {
  const sessions = [
    { id: 'own', bot_id: 'designer', conversation_owner: 'designer' },
    { id: 'helper', bot_id: 'copywriter', conversation_owner: 'designer' },
    { id: 'helper-room', bot_id: 'copywriter', conversation_owner: 'designer', room_id: 'r' },
    { id: 'theirs', bot_id: 'copywriter', conversation_owner: 'copywriter' },
  ];
  assert.deepEqual(actions.directSessions(sessions, 'designer').map(s => s.id), ['own', 'helper']);
});
