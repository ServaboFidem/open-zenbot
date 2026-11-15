import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { FluentProvider, webLightTheme, Button, Input, Textarea, Field, Badge, Switch, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, Spinner, ProgressBar, TabList, Tab } from '@fluentui/react-components';
import { Add24Regular, Apps24Regular, Chat24Regular, ArrowRight24Regular, ArrowUp24Regular, Checkmark24Regular, ChevronRight24Regular, Clock24Regular, Dismiss24Regular, Grid24Regular, People24Regular, Play24Regular, PlugConnected24Regular, Search24Regular, Settings24Regular, ShieldCheckmark24Regular, Sparkle24Regular, Stop24Regular, Document24Regular, Bot24Regular, Navigation24Regular, Record24Regular, Folder24Regular, Globe20Regular, Search20Regular, WindowConsole20Regular, BrainCircuit20Regular, Wrench20Regular, ChatSparkle20Regular, Play20Regular, CheckmarkCircle20Regular, ErrorCircle20Regular, Pause20Regular, ShieldCheckmark20Regular, ArrowMinimize20Regular, BookOpen20Regular, Database20Regular, Flag20Regular } from '@fluentui/react-icons';
import './styles.css';
import KnowledgePanel from './KnowledgePanel';
import Markdown from './Markdown';
import { MediaProvider } from './MediaCards';
import { attachmentItem } from './media.js';
import ConversationPicker from './ConversationPicker';
import { HarnessSettingsTab, ModelProvidersTab } from './ModelSettings';
import { TracingSettingsTab } from './TracingSettings';
import { AllowedFoldersTab } from './AllowedFolders';
import { ProgramPathSection } from './ProgramPathSettings';
import McpKeys from './McpKeys';
import McpDeps from './McpDeps';
import MusicArea from './music/MusicArea';
import RoutineDialog from './RoutineSchedule';
import { importLegacyRoutines, persistWorkspace, dropLegacyRoutines, importNotice } from './routines-import.js';
import { describeOutcome, formatWhen } from './routine-schedule.js';
import { arrivalText, scheduleLabel, weeklyArrival } from './music/helpers.js';
import { choiceKey, defaultModelLabel, enabledByKind, modelName, modelLabel, parseChoice } from './models.js';
import { createApi, HarnessError, harnessUrl } from './api';
import { startTelemetry } from './telemetry';
import { recallAdapter } from './knowledge-adapter';
import { latestWins, emptyReplyNote, rememberPayload, showOnboarding, showNewTopicsOffer, extractionNote, extractionEstimate, rebuildSummary, pollExtraction, scanSummary, warming, bootProgress, activityFeed, directSessions, sortRooms, afterRoomDeleted, conversationTitle, recentConversationMeta, ONBOARDING_DISMISSED, NEW_TOPICS_DISMISSED, ASSISTANT_ID } from './actions';
import logo from '../assets/zenbot-enso.svg';
import zenbotLogo from '../assets/zenbot-logo.png';

// The renderer talks to the harness; nothing here simulates a bot. What
// stays local: the connected-apps list, bot colours, and the
// workspace activity strip. The provider key passes through Settings to
// the harness once and is never written anywhere in this app.
const api = createApi();
const knowledge = recallAdapter(api);
const uid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
const STORAGE = 'openzenbot-workspace-v1';
const initial = {
  connections: [], meta: {},
  activity: [{ id: 'welcome', title: 'Your workspace is ready', detail: 'Bots, skills, and memory come from the harness on localhost.', time: 'Welcome', type: 'ready' }]
};
let storageAvailable = true;
function load() {
  try {
    const value = localStorage.getItem(STORAGE);
    if (!value) return initial;
    const { routines: _waiting, ...rest } = JSON.parse(value);  // old localStorage routines go to the harness (routines-import.js), not into state
    return { ...initial, ...rest };
  }
  catch { storageAvailable = false; return initial; }
}
const nav = [ ['Overview', Grid24Regular], ['Bots', Bot24Regular], ['Conversations', Chat24Regular],['Music', Record24Regular], ['Knowledge', People24Regular], ['Skills', Sparkle24Regular], ['MCP servers', PlugConnected24Regular], ['Routines', Clock24Regular], ['Connected apps', Apps24Regular], ['Approvals', ShieldCheckmark24Regular], ['Ledger', Document24Regular] ];
const suggestions = ['Research a topic', 'Draft a weekly update', 'Plan a project'];
const appOptions = [ ['GitHub', 'Repositories, issues, and pull requests', 'GH'], ['Google Drive', 'Documents and shared knowledge', 'GD'], ['Slack', 'Conversations and team updates', 'SL'], ['Notion', 'Notes, projects, and your knowledge base', 'N'] ];
const COLORS = ['blue', 'green', 'violet'];
const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const readFlag = key => { try { return localStorage.getItem(key) === '1'; } catch { return false; } };
const writeFlag = key => { try { localStorage.setItem(key, '1'); } catch {} };
const sortByTime = list => [...list].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
// The loading overlay while the harness warms its memory store (Hindsight is slow cold).
function BootOverlay({ connecting, warm }) {
  const label = connecting ? 'Connecting to the harness…' : warm?.state === 'warming' ? `${warm.step}…` : 'Starting…';
  return <div className="boot-overlay" role="status" aria-live="polite" aria-busy="true">
    <img className="boot-logo" src={zenbotLogo} alt="OpenZenBot"/>
    <ProgressBar className="boot-bar" thickness="large" value={connecting ? undefined : bootProgress(warm)} max={1} aria-label="Loading"/>
    <p className="boot-step">{label}</p>
    {!connecting && warm?.state === 'warming' && <p className="boot-detail">Step {warm.done + 1} of {warm.total}</p>}
  </div>;
}

// Enter sends, Shift+Enter is a new line. Only when the form's Send button is there and enabled,
// so Enter can't post while a task runs or the message is empty; never mid IME composition.
const submitOnEnter = e => {
  if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
  e.preventDefault();
  const form = e.currentTarget.form, send = form?.querySelector('button[type="submit"]');
  if (send && !send.disabled) form.requestSubmit(send);
};
const stateColor = s => s === 'done' ? 'success' : s === 'failed' || s === 'stopped' ? 'danger' : s === 'paused' || s === 'waiting_approval' ? 'warning' : 'brand';
// an exchange's outcome: routed and interrupted are neither success nor failure
const outcomeColor = o => o === 'routed' ? 'informative' : o === 'interrupted' ? 'warning' : stateColor(o);
const short = (v, n = 160) => { const s = typeof v === 'string' ? v : JSON.stringify(v); return s && s.length > n ? s.slice(0, n - 1) + '…' : s; };

function App() {
  const [data, setData] = useState(load);
  const [page, setPage] = useState('Overview');
  const [selected, setSelected] = useState('');
  const [modal, setModal] = useState(null);
  const [form, setForm] = useState({});
  const [errors, setErrors] = useState({});
  const [query, setQuery] = useState('');
  const [composer, setComposer] = useState('');
  const [notice, setNotice] = useState('');
  const [warm, setWarm] = useState(null);  // GET /warmup; null until the first answer
  const [mobileNav, setMobileNav] = useState(false);
  const [storageError, setStorageError] = useState(!storageAvailable);
  const [harness, setHarness] = useState({ status: 'connecting', settings: null, health: null });
  const [bots, setBots] = useState([]);
  const [skills, setSkills] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [routines, setRoutines] = useState([]);  // GET /routines: the harness's scheduler owns them
  const [routineDialog, setRoutineDialog] = useState(null);  // { routine } to edit, {} to create
  const [routineBusy, setRoutineBusy] = useState('');
  const [routineStore, setRoutineStore] = useState(null);  // { read_only, errors }: what routines.json could not give
  const [importNote, setImportNote] = useState(null);  // what the routine import moved (paused) and could not read
  const [arrival, setArrival] = useState(null);  // { id, count }: an unread weekly digest to tell the user about
  const [arrivalDismissed, setArrivalDismissed] = useState([]);
  const [musicKey, setMusicKey] = useState(0);  // a new key opens Music on This week
  const importTried = useRef(false);
  const [musicStatus, setMusicStatus] = useState(null);  // GET /music/status: the Music badge and area read it
  const [fits, setFits] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [recent, setRecent] = useState([]);  // Conversations page: the newest one-to-one conversations, across bots
  const [recorder, setRecorder] = useState(null);
  const [mcps, setMcps] = useState({});
  const [facts, setFacts] = useState([]);            // shared facts; empty means a new workspace
  const [dismissed, setDismissed] = useState(() => readFlag(ONBOARDING_DISMISSED));
  const [interview, setInterview] = useState(null);  // { id, step, done, messages: [{ author, text, facts }] }
  const [scanPrompt, setScanPrompt] = useState(null); // a scan awaiting confirm
  const [rebuildPrompt, setRebuildPrompt] = useState(null); // a rebuild preview awaiting confirm
  const [profile, setProfile] = useState(null);     // GET /profile; null when unavailable (local JSON backend answers 503)
  const [ownerForm, setOwnerForm] = useState({ name: '', also: '' });
  const [settingsTab, setSettingsTab] = useState('harness');
  const [providers, setProviders] = useState(null);       // GET /settings/providers: providers and their enabled models; null until loaded
  const [providerKinds, setProviderKinds] = useState([]);
  const [providerPresets, setProviderPresets] = useState([]);
  const [modelRoles, setModelRoles] = useState(null);     // GET /settings/models: which model runs chat, summaries, decisions
  const [toolSettings, setToolSettings] = useState(null);  // GET /settings/tools: the harness's own tools; null until loaded
  const [toolForms, setToolForms] = useState({});         // tool id -> { provider, model } being edited
  const [newTopicsDismissed, setNewTopicsDismissed] = useState(() => readFlag(NEW_TOPICS_DISMISSED));
  const [importJob, setImportJob] = useState(null);  // { id, dir, total, done, imported, unchanged, refused, state, current }
  const [dirField, setDirField] = useState('');      // the path field when there is no native picker
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(null);           // { sessionId, botId, events: [] }
  const [trail, setTrail] = useState({ sessionId: null, events: [] });  // the latest session's events once it is no longer streaming
  const [scopePrompt, setScopePrompt] = useState(null); // { sessionId, change, text, botId }
  const [mode, setMode] = useState('bot');          // 'bot' | 'room'
  const [roomId, setRoomId] = useState('');
  const [newTopic, setNewTopic] = useState(false);  // ZenBot page: the next message starts a fresh room
  const [routing, setRouting] = useState(false);    // ZenBot is picking where a message goes
  const [room, setRoom] = useState(null);
  const [conv, setConv] = useState(null);           // { botId, current, conversations, record }: the shown bot's conversation
  const [ledger, setLedger] = useState([]);
  const [ledgerKind, setLedgerKind] = useState('');
  const [memoryKey, setMemoryKey] = useState(0);
  const [fitChoice, setFitChoice] = useState({});
  const [activeFitId, setActiveFitId] = useState(null);
  const activeFit = fits.find(f => f.id === activeFitId) || null;
  const stopEvents = useRef(null);
  const endRef = useRef(null);
  const bot = bots.find(b => b.id === selected) || bots[0];
  const botSessions = useMemo(() => sortByTime(directSessions(sessions, bot?.id)), [sessions, bot]);
  const latest = botSessions.at(-1);  // the activity panel's session; routines included
  const botIdRef = useRef(''); botIdRef.current = bot?.id || '';
  const convLoads = useRef(latestWins());
  const exchanges = conv && conv.botId === bot?.id ? conv.record?.exchanges || [] : [];
  const roomGroups = useMemo(() => sortRooms(rooms), [rooms]);
  const roomSession = room ? (room.open_turn?.task_session || room.last_session || '') : '';
  const roomSpeakerId = room?.open_turn?.bot_id || room?.active_speaker || '';
  const pending = approvals.filter(a => a.state === 'pending');
  const online = harness.status === 'online';
  const bound = !!harness.settings?.key_set && harness.settings?.provider !== 'unbound';
  const assistant = bots.find(b => b.id === ASSISTANT_ID);
  const onboarding = showOnboarding({ facts, bots, dismissed });
  const newTopics = showNewTopicsOffer({ facts, dismissed: newTopicsDismissed }) && !!assistant && !interview;
  const progressNote = extractionNote(profile?.extraction);
  const interviewing = !!interview && bot?.id === ASSISTANT_ID;
  const canPick = typeof window !== 'undefined' && typeof window.openzenbot?.pickDirectory === 'function';
  const canPickFile = typeof window !== 'undefined' && typeof window.openzenbot?.pickFile === 'function';
  const update = fn => setData(d => fn(d));
  const addActivity = (d, title, detail, type = 'ready') => [{ id: uid(), title, detail, time: clock(), type }, ...d.activity].slice(0, 20);
  const log = (title, detail, type) => update(d => ({ ...d, activity: addActivity(d, title, detail, type) }));
  const colorOf = b => data.meta[b?.id]?.color || COLORS[(b?.name?.length || 0) % COLORS.length];

  useEffect(() => { try { persistWorkspace(localStorage, STORAGE, data); } catch { setStorageError(true); } }, [data]);
  useEffect(() => { if (notice) { const t = setTimeout(() => setNotice(''), 5000); return () => clearTimeout(t); } }, [notice]);
  useEffect(() => { if (page === 'Conversation') endRef.current?.scrollIntoView({ block: 'nearest' }); }, [exchanges.length, live?.events.length, page, room?.posts?.length]);
  useEffect(() => () => stopEvents.current?.(), []);

  async function guard(fn, { quiet = false } = {}) {
    try { return await fn(); }
    catch (e) {
      if (e instanceof HarnessError && e.status === 0) setHarness(h => ({ ...h, status: 'offline' }));
      if (!quiet) setNotice(e?.message || 'Something went wrong.');
      return undefined;
    }
  }
  async function refreshHealth() {
    try {
      const [health, settings] = await Promise.all([api.health(), api.settings()]);
      setHarness({ status: 'online', health, settings });
      return true;
    } catch { setHarness(h => ({ ...h, status: 'offline' })); return false; }
  }
  async function refreshProfile() {
    try {
      const p = await api.profile();
      setProfile(p);
      if (p.owner) setOwnerForm(f => f.name || f.also ? f : { name: p.owner.name || '', also: (p.owner.aliases || []).join(', ') });
    } catch { setProfile(null); }  // 503 on the local JSON backend: no graph, nothing to show
  }
  async function refreshMusic() {
    try { const st = await api.music.status(); setMusicStatus(st); return st; } catch { return null; }
  }
  async function refreshRoutines() {
    try { setRoutines(await api.routines()); } catch { /* keep what was shown */ }
    try { setRoutineStore(await api.routineStore()); } catch { /* keep what was shown */ }
  }
  async function refreshAll() {
    refreshMusic(); refreshRoutines();
    const [b, s, a, f, ss, r, rec, m, mem] = await Promise.allSettled([api.bots(), api.skills(), api.approvals(), api.fits(), api.sessions(), api.rooms(), api.recorderStatus(), api.mcps(), api.memory('shared')]);
    if (m.status === 'fulfilled') setMcps(m.value);
    if (mem.status === 'fulfilled') setFacts(mem.value);
    if (b.status === 'fulfilled') { setBots(b.value); setSelected(id => id || b.value[0]?.id || ''); }
    if (s.status === 'fulfilled') setSkills(s.value);
    if (a.status === 'fulfilled') setApprovals(a.value);
    if (f.status === 'fulfilled') setFits(f.value);
    if (ss.status === 'fulfilled') {
      setSessions(ss.value);
      const l = liveRef.current;
      if (l && !ss.value.some(s => s.id === l.sessionId && s.state === 'running')) { stopEvents.current?.(); stopEvents.current = null; setLive(null); }
    }
    if (r.status === 'fulfilled') setRooms(r.value);
    if (rec.status === 'fulfilled') setRecorder(rec.value);
  }
  useEffect(() => {
    // poll the warm-up while the harness is up and still warming; a harness without /warmup counts as ready
    if (harness.status !== 'online' || (warm && !warming(warm))) return;
    let alive = true;
    const tick = async () => {
      let w;
      try { w = await api.warmup(); } catch (e) { w = e instanceof HarnessError && e.status === 404 ? { state: 'ready' } : null; }
      if (!alive || !w) return;
      setWarm(w);
      if (w.state === 'failed') setNotice(`Memory warm-up failed (${w.error}). It will warm on first use.`);
    };
    tick();
    const t = setInterval(tick, 400);
    return () => { alive = false; clearInterval(t); };
  }, [harness.status, warm?.state]);
  const statusRef = useRef('connecting');
  const liveRef = useRef(null);
  useEffect(() => { liveRef.current = live; }, [live]);
  useEffect(() => { statusRef.current = harness.status; }, [harness.status]);
  useEffect(() => {
    let alive = true;
    (async () => { if (await refreshHealth()) await refreshAll(); })();
    const t = setInterval(async () => { if (!alive) return; const was = statusRef.current; if (await refreshHealth()) { if (was !== 'online') await refreshAll(); else refreshMusic(); } }, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  // the routines this browser kept before the harness scheduled them move over once per load
  useEffect(() => {
    if (!online || importTried.current) return;
    importTried.current = true;
    let storage = null; try { storage = localStorage; } catch { /* no storage: nothing to move */ }
    if (!storage) return;
    importLegacyRoutines({ api, storage, key: STORAGE }).then(r => {
      if (!r) return;
      if (r.imported.length || r.unparsed.length) setImportNote(r);
      refreshRoutines();
    });
  }, [online]);
  useEffect(() => {
    if (page !== 'Routines' || !online) return undefined;
    refreshRoutines();
    const t = setInterval(refreshRoutines, 15000);  // next and last run move on their own
    return () => clearInterval(t);
  }, [page, online]);
  // an unread weekly digest tells the user once; an on-demand result never does
  const unreadCount = musicStatus?.unread;
  useEffect(() => {
    if (!unreadCount) { setArrival(null); return undefined; }
    let alive = true;
    api.music.digests({ limit: 1 }).then(list => {
      const a = weeklyArrival(list?.[0]);
      if (alive) setArrival(a && !arrivalDismissed.includes(a.id) ? a : null);
    }).catch(() => {});
    return () => { alive = false; };
  }, [unreadCount, arrivalDismissed]);
  async function markArrivalRead() {
    const id = arrival?.id;
    setArrivalDismissed(d => [...d, id]);
    if (id) { try { await api.music.markRead(id); } catch { /* the banner stays dismissed for this load */ } refreshMusic(); }
  }
  // the desktop notice was clicked: open Music on This week
  useEffect(() => {
    const sub = window.openzenbot?.onOpenMusic;
    if (typeof sub !== 'function') return undefined;
    return sub(() => { setMusicKey(k => k + 1); navigate('Music'); });
  }, []);
  useEffect(() => { if (online) refreshProfile(); }, [online, memoryKey, importJob?.state]);
  useEffect(() => {
    if (!online || !pollExtraction(profile?.extraction)) return;
    const t = setInterval(refreshProfile, 4000);
    return () => clearInterval(t);
  }, [online, profile?.extraction?.queued, profile?.extraction?.waiting_for_owner]);
  useEffect(() => { if (memoryKey && online) guard(async () => setFacts(await api.memory('shared')), { quiet: true }); }, [memoryKey, online]);
  useEffect(() => { if (page === 'Ledger' && online) guard(async () => setLedger(await api.ledger({ n: 100, kind: ledgerKind || undefined })), { quiet: true }); }, [page, ledgerKind, online]);
  useEffect(() => { if (page === 'Settings' && online) { refreshTools(); refreshModels(); } }, [page, online]);
  useEffect(() => { if (page === 'Approvals' && online) guard(async () => setApprovals(await api.approvals()), { quiet: true }); }, [page, online]);
  useEffect(() => {
    if (page !== 'Conversations' || !online) return;
    guard(async () => setRecent((await api.recentConversations(5)).conversations || []), { quiet: true });
    api.rooms().then(setRooms).catch(() => {});
  }, [page, online]);
  useEffect(() => {
    if (page !== 'Conversation' || mode !== 'room' || !roomId || !online) return;
    let alive = true;
    const tick = () => guard(async () => { const r = await api.roomTurn(roomId); if (alive) setRoom(r); }, { quiet: true });
    tick(); const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [page, mode, roomId, online]);
  useEffect(() => { if (page === 'Conversation' && mode === 'bot' && bot?.id && online) loadConversation(bot.id); }, [page, mode, bot?.id, online]);
  // the activity feed for a session this window is not streaming (a bot's latest, or the room's
  // current turn): finished ones load once, ones still running re-read the ledger every two seconds
  const trailId = mode === 'room' ? roomSession : (live?.sessionId === latest?.id ? '' : latest?.id || '');
  const trailRunning = mode === 'room' ? !!room?.open_turn : ['running', 'waiting_approval', 'paused'].includes(latest?.state);
  useEffect(() => {
    if (page !== 'Conversation' || !trailId || !online) return;
    let alive = true;
    const load = () => api.ledger({ session_id: trailId, n: 500 }).then(ev => { if (alive) setTrail({ sessionId: trailId, events: ev }); }).catch(() => {});
    load();
    const t = trailRunning ? setInterval(load, 2000) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [page, mode, trailId, trailRunning, online]);
  useEffect(() => {
    if (!recorder || recorder.state !== 'recording') return;
    const t = setInterval(() => guard(async () => { const st = await api.recorderStatus(); setRecorder(r => ({ ...r, ...st })); }, { quiet: true }), 1500);
    return () => clearInterval(t);
  }, [recorder?.state]);

  function navigate(next) { setPage(next); setQuery(''); setMobileNav(false); }
  function openBot(id) { setSelected(id); setMode('bot'); setComposer(''); navigate('Conversation'); }
  // Conversations page: open that bot with that conversation made its current one
  async function openConversation(botId, id) {
    openBot(botId);
    await switchConversation(botId, id);
  }
  // Overview's main button: ZenBot in a fresh conversation, as the picker's "New conversation" makes one
  async function startConversation() {
    if (!assistant) return;
    openBot(assistant.id);
    await switchConversation(assistant.id, null);
  }
  function openForm(type, item) {
    setErrors({});
    if (type === 'bot' && (!providers || !modelRoles)) refreshModels();  // the Model picker lists enabled text models
    setForm(item ? (type === 'bot' ? { ...item, model: choiceKey(item.model?.provider, item.model?.model) } : item) : (type === 'bot' ? { name: '', role: '', instructions: '', color: 'blue', model: '' }
      : type === 'teach' ? { name: '', use: '' }
      : type === 'room' ? { name: '', members: bots.slice(0, 2).map(b => b.id) }
      : {}));
    setModal(type);
  }
  function checkField(key, value) {
    const message = !String(value || '').trim() ? `Enter ${key === 'instructions' ? 'instructions describing what this bot should do' : key === 'use' ? 'when to use this skill' : 'a ' + key}.` : '';
    setErrors(e => ({ ...e, [key]: message })); return !message;
  }
  async function saveForm(e) {
    e.preventDefault();
    const keys = modal === 'bot' ? ['name', 'role'] : modal === 'teach' ? ['name', 'use'] : ['name'];
    if (!keys.map(k => checkField(k, form[k])).every(Boolean)) return;
    if (modal === 'bot') {
      const body = { name: form.name.trim(), role: form.role.trim(), instructions: form.instructions || '', model: parseChoice(form.model) };
      const saved = await guard(() => form.id ? api.patchBot(form.id, body) : api.createBot({ ...body, skills: [] }));
      if (!saved) return;
      update(d => ({ ...d, meta: { ...d.meta, [saved.id]: { color: form.color || 'blue' } }, activity: addActivity(d, `${saved.name} ${form.id ? 'updated' : 'created'}`, 'Saved in the harness.') }));
      setBots(await api.bots()); setModal(null); setNotice(`${saved.name} saved`); openBot(saved.id);
    } else if (modal === 'teach') {
      const status = await guard(() => api.startRecording(form.name.trim()));
      if (!status) return;
      setRecorder({ ...status, use: form.use.trim(), name: form.name.trim() });
      setModal(null); setNotice('Recording. Work through the task in the driven browser, then stop and draft.');
      log('Recording started', form.name.trim());
    } else if (modal === 'room') {
      const created = await guard(() => api.createRoom(form.name.trim(), form.members || []));
      if (!created) return;
      setRooms(await api.rooms()); setRoomId(created.id); setRoom(created); setMode('room'); setModal(null); navigate('Conversation');
      log('Room created', `${created.name} · ${created.members.length} members`);
    }
  }
  // The bot's conversations and its current one with its exchanges; dropped if another bot is shown by then.
  // Loads overlap (one when a task starts, one when it ends, the picker's, the effect's): only the newest writes.
  async function loadConversation(botId) {
    const isLatest = convLoads.current();
    const list = await api.conversations(botId).catch(() => null);
    if (!list) return;
    const record = list.current ? await api.conversation(list.current).catch(() => null) : null;
    if (isLatest() && botIdRef.current === botId) setConv({ botId, current: list.current, conversations: list.conversations || [], record });
  }
  async function switchConversation(botId, id) {
    const ok = await guard(() => id ? api.selectConversation(botId, id) : api.newConversation(botId));
    if (!ok) return;
    setScopePrompt(p => p?.botId === botId ? null : p);
    await loadConversation(botId);
  }
  async function switchRoomConversation(id) {
    if (!room) return;
    const r = await guard(() => id ? api.selectRoomConversation(room.id, id) : api.newRoomConversation(room.id));
    if (!r) return;
    setRoom(r); api.rooms().then(setRooms).catch(() => {});
  }
  async function send(text) {
    const task = text.trim();
    if (!bot || !task) return;
    if (interviewing && !interview.done) return sendInterview(task);
    if (!bound) { setNotice('Bind a provider and key in Settings first.'); navigate('Settings'); return; }
    if (bot.id === ASSISTANT_ID) {
      setRouting(true);
      const routed = await guard(() => api.orchestrate(task, { newTopic })).finally(() => setRouting(false));
      setNewTopic(false);
      if (!routed) return;
      if (routed.outcome === 'room' && routed.room_id) {
        setComposer('');
        const r = await api.roomTurn(routed.room_id).catch(() => null);
        api.rooms().then(setRooms).catch(() => {});
        setRoomId(routed.room_id); if (r) setRoom(r); setMode('room');
        log('Sent to a room', r?.name || routed.room_id);
        return;
      }
    }
    // the scope check compares with the conversation's last task
    const lastTask = exchanges.findLast(x => x.session_id);
    if (lastTask && harness.settings?.scorer) {
      const change = await guard(() => api.scopeCheck(lastTask.session_id, task), { quiet: true });
      if (change?.changed) { setScopePrompt({ sessionId: lastTask.session_id, change, text: task, botId: bot.id }); return; }
    }
    await start(bot.id, task);
  }
  // A chat task joins the shown conversation; a routine passes { conversation: false } and joins none.
  async function start(botId, task, { conversation = true, protected: block } = {}) {
    const conversationId = conversation && conv?.botId === botId ? conv.current || undefined : undefined;
    const r = await guard(() => api.startTask(botId, task, { conversationId, conversation, protected: block }));
    if (!r) return;
    if (conversation) setComposer('');
    // a routine started while another task streams leaves that stream alone (the chat refetches when it ends);
    // the routine's activity still reaches the panel through the ledger trail
    if (!conversation && liveRef.current) { api.sessions().then(setSessions).catch(() => {}); log('Routine started', task); return; }
    stopEvents.current?.();
    setLive({ sessionId: r.session_id, botId, events: [] });
    api.sessions().then(setSessions).catch(() => {});
    if (conversation) loadConversation(botId);
    stopEvents.current = api.events(r.session_id, async e => {
      setLive(l => l && l.sessionId === r.session_id ? { ...l, events: [...l.events, e].slice(-200) } : l);
      if (e.kind === 'end') { stopEvents.current = null; await finishLive(r.session_id, e.state); if (conversation) loadConversation(botId); }
      else if (['pause', 'resume', 'approval_request', 'supervisor', 'session'].includes(e.kind)) { const all = await api.sessions().catch(() => null); if (all) setSessions(all); }
    });
    log('Task started', task);
  }
  async function finishLive(sessionId, state) {
    const all = await api.sessions().catch(() => null);
    if (all) setSessions(all);
    const s = all?.find(x => x.id === sessionId);
    log(state === 'done' ? 'Task completed' : `Task ${state}`, s?.task || '', state === 'done' ? 'ready' : 'approval');
    setMemoryKey(k => k + 1);
    if (liveRef.current?.sessionId === sessionId) setTrail({ sessionId, events: liveRef.current.events });  // the feed stays put when the stream closes
    setLive(l => (l && l.sessionId === sessionId ? null : l));
    if (state === 'lost') setNotice('The live stream closed; showing the session as the harness has it.');
  }
  async function resolveScope(accept) {
    const p = scopePrompt; setScopePrompt(null);
    if (!p) return;
    const ok = await guard(() => accept ? api.scopeConfirm(p.sessionId, { task: p.text }) : api.scopeReject(p.sessionId));
    if (ok === undefined) return;
    log(accept ? 'Scope change confirmed' : 'Scope change rejected', p.text);
    // confirm: the typed text is the new protected task. Reject: the earlier block carries on, refined by it.
    const prev = sessions.find(s => s.id === p.sessionId);
    const block = prev?.protected || (prev ? { task: prev.task, constraints: [] } : null);
    await start(p.botId, p.text, accept || !block ? {} : { protected: { task: block.task, constraints: [...(block.constraints || []), `refine: ${p.text}`] } });
  }
  // -- onboarding and directory import ----------------------------------
  async function startOnboarding(topics) {
    if (!assistant) return;
    if (!bound) { setNotice('Bind a provider and key in Settings first, then come back to meet your assistant.'); navigate('Settings'); return; }
    setSelected(assistant.id); setMode('bot'); setComposer(''); navigate('Conversation');
    setBusy(true);
    const r = await guard(() => api.onboardingStart(topics));
    setBusy(false);
    if (!r) return;
    if (topics === 'new') dismissNewTopics();
    setInterview({ id: r.interview.id, step: r.interview.step, done: r.interview.done, messages: [{ author: 'assistant', text: r.message }] });
    log('Onboarding started', 'Your assistant is getting to know you');
  }
  function dismissOnboarding() { writeFlag(ONBOARDING_DISMISSED); setDismissed(true); }
  function dismissNewTopics() { writeFlag(NEW_TOPICS_DISMISSED); setNewTopicsDismissed(true); }
  const toolForm = t => ({ choice: choiceKey(t.provider, t.model) });
  async function refreshModels() {
    const [p, r] = await Promise.all([guard(() => api.providers(), { quiet: true }), guard(() => api.modelRoles(), { quiet: true })]);
    if (p) { setProviders(p.providers); setProviderKinds(p.kinds); setProviderPresets(p.presets || []); }
    if (r) setModelRoles(r);
  }
  const settingsCtx = { api, online, guard, notify: setNotice, log };
  async function refreshTools() {
    const r = await guard(() => api.toolSettings(), { quiet: true });
    if (r) { setToolSettings(r.tools); setToolForms(Object.fromEntries(r.tools.map(t => [t.id, toolForm(t)]))); }
  }
  async function saveTool(id, patch, done) {
    const t = await guard(() => api.setTool(id, patch));
    if (!t) return;
    setToolSettings(ts => (ts || []).map(x => (x.id === id ? t : x)));
    if (!('enabled' in patch) || Object.keys(patch).length > 1) setToolForms(f => ({ ...f, [id]: toolForm(t) }));
    setNotice(done(t)); log('Tool settings saved', `${t.name}: ${t.enabled ? 'on' : 'off'} · ${t.provider} · ${t.model}`);
  }
  async function saveOwner(e) {
    e.preventDefault();
    const name = ownerForm.name.trim();
    if (!name) { setNotice('Enter your name first.'); return; }
    const aliases = ownerForm.also.split(',').map(a => a.trim()).filter(Boolean);
    const r = await guard(() => api.setOwner(name, aliases));
    if (r) { setNotice('Saved your name.'); log('Owner saved', name); refreshProfile(); }
  }
  async function sendInterview(text) {
    setComposer('');
    setInterview(i => ({ ...i, messages: [...i.messages, { author: 'user', text }] }));
    setBusy(true);
    const r = await guard(() => api.onboardingReply(interview.id, text));
    setBusy(false);
    if (!r) return;
    setInterview(i => ({ ...i, step: r.step, done: r.done, messages: [...i.messages, { author: 'assistant', text: r.message, facts: r.facts_added }] }));
    if (r.facts_added.length) setMemoryKey(k => k + 1);
    if (r.done) log('Onboarding finished', `${facts.length + r.facts_added.length} things remembered`);
  }
  async function pickFolder() {
    let dir = dirField.trim();
    if (canPick) { dir = await window.openzenbot.pickDirectory(); if (!dir) return; }
    if (!dir) { setNotice('Type the folder path first.'); return; }
    const scan = await guard(() => api.scanDir(dir));
    if (scan) setScanPrompt(scan);
  }
  async function confirmImport() {
    const scan = scanPrompt; setScanPrompt(null);
    if (!scan) return;
    const r = await guard(() => api.importDir(scan.token));
    if (!r) return;
    log('Import started', `${scan.files} files from ${scan.dir}`);
    trackImport(r.import_id, scan.dir, scan.files);
  }
  async function previewRebuild() {
    const preview = await guard(() => api.rebuildPreview());
    if (preview) setRebuildPrompt(preview);
  }
  async function confirmRebuild() {
    setRebuildPrompt(null);
    const r = await guard(() => api.rebuild());  // 409 while extraction is queued, 503 without the graph: the notice says so
    if (!r) return;
    log('Memory rebuild started', `${r.queued} shared facts queued for extraction`);
    setNotice(`Rebuilding: ${r.queued} shared ${r.queued === 1 ? 'fact' : 'facts'} queued for extraction.`);
    refreshProfile();
  }
  async function pickFile() {
    let path = dirField.trim();
    if (canPickFile) { path = await window.openzenbot.pickFile(); if (!path) return; }
    if (!path) { setNotice('Type the file path first.'); return; }
    const r = await guard(() => api.importFile(path));
    if (!r) return;
    log('Import started', path);
    trackImport(r.import_id, r.dir, 1);
  }
  function trackImport(id, dir, total) {
    setImportJob({ id, dir, total, done: 0, imported: 0, unchanged: 0, refused: 0, state: 'running', current: '' });
    api.importEvents(id, e => {
      if (e.kind === 'file') setImportJob(j => j && ({ ...j, done: j.done + 1, current: e.path, imported: j.imported + (e.status === 'new' || e.status === 'updated' ? 1 : 0), unchanged: j.unchanged + (e.status === 'unchanged' ? 1 : 0), refused: j.refused + (e.status === 'refused' ? 1 : 0) }));
      else if (e.kind === 'stopping') setImportJob(j => j && j.state === 'running' ? { ...j, state: 'stopping' } : j);
      else if (e.kind === 'end') { setImportJob(j => j && ({ ...j, state: e.state === 'lost' ? 'lost' : e.state === 'stopped' ? 'stopped' : 'done', current: '', imported: e.imported ?? j.imported, unchanged: e.unchanged ?? j.unchanged, refused: e.refused ?? j.refused })); setMemoryKey(k => k + 1); log(e.state === 'stopped' ? 'Import stopped' : 'Import finished', `${e.imported ?? 0} files remembered`); }
    });
  }
  async function stopImport() {
    if (!importJob || importJob.state !== 'running') return;
    setImportJob(j => j && ({ ...j, state: 'stopping' }));  // the click shows at once; the harness confirms with an end event
    await guard(() => api.stopImport(importJob.id));
  }
  async function stopTask() {
    if (!live) return;
    await guard(() => api.stopSession(live.sessionId));
    log('Stop requested', '');
  }
  async function sessionAction(id, action) {
    const r = await guard(() => action === 'resume' ? api.resumeSession(id) : action === 'retry' ? api.retrySession(id) : api.stopSession(id));
    if (r) { setSessions(await api.sessions()); setNotice(action === 'retry' ? (r.resumed ? 'Provider answered; resuming.' : 'Provider still unavailable.') : `Session ${action} sent.`); }
  }
  async function rememberResult(s) {
    const f = await guard(() => api.remember(rememberPayload(s)));
    if (f) { setNotice('Remembered in shared memory. It shows on the Knowledge page and in every recall.'); setMemoryKey(k => k + 1); }
  }
  async function runRoutine(r) {
    setRoutineBusy(r.id);
    const done = await guard(() => api.runRoutine(r.id));
    setRoutineBusy('');
    if (!done) return;
    setNotice(`${r.name} started. It runs apart from your conversations.`);
    log('Routine started', r.name);
    refreshRoutines(); api.sessions().then(setSessions).catch(() => {});
  }
  async function saveRoutine(item) {
    const saved = await guard(() => api.saveRoutine(item));
    if (!saved) return;
    log(`${saved.name} ${item.id ? 'updated' : 'created'}`, scheduleLabel(saved));
    setRoutineDialog(null); setNotice(`${saved.name} saved`); refreshRoutines(); refreshMusic();
  }
  async function toggleRoutine(r, enabled) {
    if (await guard(() => api.saveRoutine({ ...r, enabled }))) refreshRoutines();
  }
  async function deleteRoutine(r) {
    const msg = r.managed_by ? `Delete ${r.name}? This turns the weekly digest off.` : `Delete ${r.name}?`;
    if (!window.confirm(msg)) return;
    if (await guard(() => api.deleteRoutine(r.id))) { setNotice(`${r.name} deleted`); refreshRoutines(); refreshMusic(); }
  }
  async function decide(item, approved) {
    const r = await guard(() => api.decide(item.id, approved));
    if (!r) return;
    setApprovals(await api.approvals()); setSessions(await api.sessions());
    log(approved ? 'Request approved' : 'Request rejected', item.title, 'approval');
    setNotice(approved ? 'Approved. The bot continues with that action.' : 'Rejected. The bot continues without it.');
  }
  async function suggestFit(skill) {
    const fit = await guard(() => api.suggestFit(skill.ref));
    if (!fit) return;
    setFits(await api.fits()); setFitChoice(c => ({ ...c, [fit.id]: fit.bot_ids[0] || '' }));
    setActiveFitId(fit.id);
    setNotice(fit.bot_ids.length ? `Suggested ${fit.bot_ids.length === 1 ? 'one bot' : fit.bot_ids.length + ' bots'} for ${skill.name}. Confirm to attach.` : `No bot fits ${skill.name} well enough. Pick one yourself or deny.`);
  }
  async function settleFit(fit, accept) {
    if (!fit) return;
    const r = await guard(() => accept ? api.confirmFit(fit.id, fitChoice[fit.id] || fit.bot_ids[0] || bots[0]?.id) : api.denyFit(fit.id));
    if (!r) return;
    setActiveFitId(null);
    setFits(await api.fits()); setBots(await api.bots()); setSkills(await api.skills().catch(() => skills));
    log(accept ? 'Skill attached' : 'Fit denied', fit.skill);
  }
  async function stopRecording() {
    const rec = await guard(() => api.stopRecording());
    if (!rec) return;
    const draft = await guard(() => api.draftSkill(recorder?.name || rec.name || 'recorded-task', recorder?.use || 'Repeat the recorded task.'));
    setRecorder(await api.recorderStatus().catch(() => null));
    if (draft) {
      setNotice(draft.path ? `Skill drafted at ${draft.path}` : `Draft ${draft.state}: ${draft.detail || draft.reason || ''}`);
      setSkills(await api.skills().catch(() => skills));
      log('Recording stopped', `${rec.actions?.length ?? ''} actions, ${rec.redactions ?? 0} redactions`);
    }
  }
  async function archiveRoom(r, archived) {
    const out = await guard(() => api.archiveRoom(r.id, archived));
    if (out) { setRooms(await api.rooms().catch(() => rooms)); if (roomId === r.id) setRoom(out); }
  }
  async function deleteRoom(r) {
    if (!window.confirm(`Delete "${r.name}"? Its posts and files go with it.`)) return;
    const done = await guard(async () => { await api.deleteRoom(r.id); return true; });
    if (!done) return;
    const reset = afterRoomDeleted(roomId, r.id);
    if (reset) { setRoomId(reset.roomId); setRoom(reset.room); setMode(reset.mode); }
    setRooms(await api.rooms().catch(() => rooms.filter(x => x.id !== r.id)));
  }
  async function postToRoom(text) {
    if (!roomId || !text.trim()) return;
    const p = await guard(() => api.postToRoom(roomId, text.trim()));
    if (p) { setComposer(''); setRoom(await api.roomTurn(roomId).catch(() => room)); }
  }

  const matches = item => `${item.name} ${item.role || ''} ${item.use || ''} ${item.description || ''}`.toLowerCase().includes(query.toLowerCase());
  const Avatar = ({ item, small = false }) => <div className={`bot-avatar ${colorOf(item)} ${small ? 'small' : ''}`}><Bot24Regular /></div>;
  const Empty = ({ title, text, action, onClick }) => <div className="empty od-stack"><Sparkle24Regular /><h2>{title}</h2><p>{text}</p>{action && <Button appearance="primary" onClick={onClick}>{action}</Button>}</div>;
  const botState = b => { const s = sortByTime(sessions.filter(x => x.bot_id === b.id)).at(-1); return !s ? 'Ready' : s.state === 'running' ? 'Working' : s.state === 'paused' ? 'Paused' : s.state === 'waiting_approval' ? 'Needs approval' : 'Ready'; };
  function BotCard({ item }) { const st = botState(item); return <article className="bot-card od-stack"><div className="od-row"><Avatar item={item}/><Badge appearance="tint" color={st === 'Ready' ? 'success' : st === 'Working' ? 'brand' : 'warning'} size="small">{st}</Badge></div><div><button className="title-link" onClick={() => openBot(item.id)}>{item.name}</button><p>{item.role}</p></div><div className="bot-card-bottom od-row"><span className="category">{item.skills.length} {item.skills.length === 1 ? 'skill' : 'skills'}</span><Button appearance="subtle" icon={<ArrowRight24Regular/>} aria-label={`Open ${item.name}`} onClick={() => openBot(item.id)}/></div></article>; }
  const statusLabel = harness.status === 'offline' ? 'Harness offline' : harness.status === 'connecting' ? 'Connecting…' : bound ? `Connected · ${harness.settings.provider}` : 'Harness online · no provider';
  const bandOf = s => s?.route?.bands?.skill;
  // what needs the user right now: a pause to resume, approvals waiting, supervisor interventions
  const SessionStatus = ({ s }) => <div className="od-stack session-status">
    {s.pause_reason && <div className="inline-note"><Clock24Regular/><span>{s.pause_reason}</span></div>}
    {(s.state === 'paused') && <div className="od-row"><Button appearance="primary" onClick={() => sessionAction(s.id, 'resume')}>Resume</Button><Button appearance="outline" onClick={() => sessionAction(s.id, 'retry')}>Retry provider</Button><Button appearance="subtle" onClick={() => sessionAction(s.id, 'stop')}>Stop</Button></div>}
    {s.approvals?.length > 0 && <p><button className="text-link" onClick={() => navigate('Approvals')}>{s.approvals.length} approval {s.approvals.length === 1 ? 'request' : 'requests'} <span aria-hidden="true">→</span></button></p>}
  </div>;
  // the routing record behind a session: injected skills, band scores, supervisor, the protected task
  const SessionPanel = ({ s }) => <div className="od-stack">
    {s.compactions > 0 && <span className="quiet-label">{s.compactions} compactions</span>}
    <h3>What the decider injected</h3>
    {s.injected?.length ? <ul>{s.injected.map(slug => <li key={slug}><strong>{slug}</strong>{bandOf(s)?.scores?.[slug] != null && <span className="quiet-label"> · {Number(bandOf(s).scores[slug]).toFixed(2)}</span>}</li>)}</ul> : <p>No skills injected.</p>}
    {bandOf(s) && <p className="field-hint">Band {bandOf(s).outcome} · top {Number(bandOf(s).top).toFixed(2)} · floor {Number(bandOf(s).floor).toFixed(2)}{s.route?.cached ? ' · cached' : ''}{s.route?.fallback ? ` · fell open: ${s.route.fallback}` : ''}</p>}
    {(s.nudges > 0 || s.halts > 0) && <div className="inline-note"><ShieldCheckmark24Regular/><span>Supervisor: {s.nudges} {s.nudges === 1 ? 'nudge' : 'nudges'}, {s.halts} {s.halts === 1 ? 'halt' : 'halts'}{s.halts > 0 ? '. A halt pauses the session; Resume confirms continuing.' : ''}</span></div>}
    {s.protected?.task && <><h3>Protected task</h3><div className="instructions-block">{s.protected.task}{s.protected.constraints?.length > 0 && <ul>{s.protected.constraints.map(c => <li key={c}>{c}</li>)}</ul>}</div></>}
  </div>;

  const booting = harness.status === 'connecting' || (harness.status === 'online' && warming(warm));
  return <FluentProvider theme={webLightTheme} className="provider">
    {booting && <BootOverlay connecting={harness.status === 'connecting'} warm={warm}/>}
    <div className="workspace" aria-hidden={booting || undefined}>
      <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
        <a className="brand od-row" href="#" onClick={e => { e.preventDefault(); navigate('Overview'); }}><span className="brand-mark"><img src={logo} alt=""/></span><span>OpenZenBot</span></a>
        <div className="workspace-name od-row"><span className="workspace-initial">P</span><div className="od-field od-fill"><strong>Personal workspace</strong><span>Local · {api.baseUrl.replace('http://', '')}</span></div></div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Workspace">{nav.map(([name, Icon]) => <button key={name} className={`nav-item ${page === name || (name === 'Conversations' && page === 'Conversation') ? 'selected' : ''}`} aria-current={page === name ? 'page' : undefined} onClick={() => navigate(name)}><Icon/><span className="od-fill">{name}</span>{name === 'Approvals' && pending.length > 0 && <span className="nav-count">{pending.length}</span>}{name === 'Music' && musicStatus?.unread > 0 && <span className="nav-count" aria-label={`${musicStatus.unread} new ${musicStatus.unread === 1 ? 'digest' : 'digests'}`}>{musicStatus.unread}</span>}</button>)}</nav>
        <div className="sidebar-bottom"><div className="local-note"><span className="status-dot"/><strong>Your workspace. Your rules.</strong><p>Everything runs through the harness on localhost.</p></div><button className={`nav-item ${page === 'Settings' ? 'selected' : ''}`} onClick={() => navigate('Settings')}><Settings24Regular/><span>Settings</span></button><div className="profile od-row"><span className="profile-avatar">U</span><div className="od-field od-fill"><strong>You</strong><span>{harness.settings?.memory ? `Memory: ${harness.settings.memory}` : 'Local profile'}</span></div><Badge appearance="outline" size="small">Local</Badge></div></div>
      </aside>
      <div className="main-shell">
        <header className="topbar od-row"><Button className="menu-button" appearance="subtle" icon={<Navigation24Regular/>} aria-label="Toggle navigation" onClick={() => setMobileNav(!mobileNav)}/><span className="breadcrumb">Workspace</span><ChevronRight24Regular/><strong>{page === 'Conversation' ? (mode === 'room' ? room?.name || 'Room' : bot?.name) : page}</strong><div className="od-fill"/><span className="simulation-label"><span className="status-dot"/> {statusLabel}</span><Button appearance="subtle" icon={<ShieldCheckmark24Regular/>} aria-label="Open approvals" onClick={() => navigate('Approvals')}/></header>
        {arrival && page !== 'Music' && <div className="storage-warning od-row" role="status"><span className="od-fill">{arrivalText(arrival.count)}.</span><Button appearance="outline" onClick={() => navigate('Music')}>Open digest</Button><Button appearance="subtle" onClick={markArrivalRead}>Mark as read</Button></div>}
        {storageError && <div className="storage-warning" role="status">Local storage is unavailable. Activity will last for this session only.</div>}
        {harness.status === 'offline' && <div className="storage-warning" role="status">The harness at {api.baseUrl} is not answering. Start it with <code>uv run python -m src.harness.main</code>; this page retries every few seconds.</div>}
        {online && !bound && page !== 'Settings' && <div className="storage-warning" role="status">No provider is bound. <button className="text-link" onClick={() => { navigate('Settings'); setSettingsTab('providers'); }}>Add a provider key and choose models in Settings</button> before sending tasks.</div>}
        <main className={page === 'Conversation' ? 'conversation-main' : 'content'} id="main-content"><MediaProvider base={api.baseUrl} trusted={harness.settings?.media_trusted_hosts} notify={setNotice} scope={mode === 'room' ? `room:${roomId}:${room?.conversation_id || ''}` : `bot:${bot?.id || ''}:${conv?.current || ''}`}>
          <div hidden={page !== 'Knowledge'}><KnowledgePanel adapter={knowledge} botId={bot?.id || ''} bots={bots} refreshKey={memoryKey + (page === 'Knowledge' ? 1000 : 0)}/></div>
          {page === 'Overview' && <>
            <section className="page-heading od-row-top"><div className="od-fill"><div className="eyebrow">A LITTLE LESS BUSYWORK</div><h1>Make room for your best work.</h1><p>Your bots, skills, and routines. One workspace, built around you.</p></div><Button appearance="primary" icon={<Chat24Regular/>} disabled={!assistant} title={assistant ? 'Open a new conversation with ZenBot' : 'ZenBot appears once the harness is running'} onClick={startConversation}>Start a conversation</Button></section>
            <section className="summary-strip od-grid" aria-label="Workspace summary"><div className="od-row"><span className="summary-icon"><Bot24Regular/></span><div className="od-stat"><strong>{bots.length}<span> bots in your workspace</span></strong><span>{online ? 'Ready when you are' : 'Waiting for the harness'}</span></div></div><div className="od-row"><span className="summary-icon"><Clock24Regular/></span><div className="od-stat"><strong>{routines.filter(r => r.enabled).length}<span> active routines</span></strong><span>Scheduled by the harness</span></div></div><div className="od-row"><span className="summary-icon"><ShieldCheckmark24Regular/></span><div className="od-stat"><strong>{pending.length}<span> awaiting approval</span></strong><button className="text-link" onClick={() => navigate('Approvals')}>You're in control <span aria-hidden="true">→</span></button></div></div></section>
            {onboarding && <section className="onboarding-card od-row"><span className="getting-started-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>Let's get you set up</h3><p>Your assistant will ask a few questions, remember the answers for every bot, and can learn more from a folder of your files.</p></div><Button appearance="primary" onClick={() => startOnboarding()}>Start</Button><Button appearance="subtle" onClick={dismissOnboarding}>Not now</Button></section>}{newTopics && <section className="onboarding-card od-row"><span className="getting-started-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>Tell your assistant about your people</h3><p>New questions cover your family and the organizations in your life. Your earlier answers stay as they are.</p></div><Button appearance="primary" onClick={() => startOnboarding('new')}>Start</Button><Button appearance="subtle" onClick={dismissNewTopics}>Not now</Button></section>}
            <section><div className="section-heading od-row"><h2>Your bots</h2><span className="section-count">{bots.length}</span><div className="od-fill"/><Button appearance="subtle" onClick={() => navigate('Bots')}>View all <ArrowRight24Regular/></Button></div><div className="bot-grid">{bots.slice(0, 3).map(b => <BotCard key={b.id} item={b}/>)}</div></section>
            <div className="overview-lower"><section className="activity-panel"><div className="section-heading od-row"><h2>Workspace activity</h2><div className="od-fill"/><button className="text-link" onClick={() => navigate('Ledger')}>FULL LEDGER <span aria-hidden="true">→</span></button></div><div className="activity-list">{data.activity.slice(0, 4).map(a => <div className="activity-row od-row-top" key={a.id}><span className={`activity-symbol ${a.type}`}><Checkmark24Regular/></span><div className="od-field od-fill"><strong>{a.title}</strong><span>{a.detail}</span></div><span className="activity-time">{a.time}</span></div>)}</div><div className="activity-foot"><ShieldCheckmark24Regular/><span>Every model call, tool call, and decision is in the harness ledger.</span></div></section>
            <section className="routine-panel"><div className="section-heading od-row"><h2>On your schedule</h2><div className="od-fill"/><Button appearance="subtle" icon={<ArrowRight24Regular/>} aria-label="View routines" onClick={() => navigate('Routines')}/></div>{routines.slice(0, 2).map(r => <button className="schedule-row od-row" key={r.id} onClick={() => navigate('Routines')}><span className="schedule-icon"><Clock24Regular/></span><span className="od-field od-fill"><strong>{r.name}</strong><span>{scheduleLabel(r)}</span></span><ChevronRight24Regular/></button>)}<Button appearance="subtle" icon={<Add24Regular/>} onClick={() => setRoutineDialog({})}>Create a routine</Button></section></div>
            <section className="getting-started od-row"><span className="getting-started-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>Start small. Build something useful.</h3><p>Give a bot one clear job. Teach a task to turn it into a skill, then a routine.</p></div>{bots[0] && <Button appearance="outline" onClick={() => openBot(bots[0].id)}>Try your first task <ArrowRight24Regular/></Button>}</section>
            <footer className="page-footer od-row"><span>OPENZENBOT · BUILT TO BE YOURS</span><div className="od-fill"/><span>Open source. Locally yours.</span></footer>
          </>}
          {page === 'Bots' && <><PageHeading title="Your bots" description="Give each teammate a clear purpose and a little room to work." action="Create a bot" onAction={() => openForm('bot')}/>
            <div className="inline-note od-row"><Record24Regular/><span className="od-fill">{recorder?.state === 'recording' ? `Recording “${recorder.name}”: ${recorder.actions} actions, ${Math.round(recorder.elapsed_s)}s, ${recorder.redactions} redactions.` : recorder?.state === 'unavailable' || !recorder ? 'Teach a task by recording it in a driven browser. The recorder needs Playwright on the harness machine.' : 'Teach a task: record it once in a driven browser and the harness drafts a skill from the intent of each step.'}</span>{recorder?.state === 'recording' ? <Button appearance="primary" icon={<Stop24Regular/>} onClick={stopRecording}>Stop and draft</Button> : <Button appearance="outline" icon={<Record24Regular/>} disabled={!online} onClick={() => openForm('teach')}>Teach a task</Button>}</div>
            <div className="od-row"><Search query={query} setQuery={setQuery} label="Search bots"/></div>
            {onboarding && <section className="onboarding-card od-row"><span className="getting-started-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>Let's get you set up</h3><p>Your assistant will ask a few questions, remember the answers for every bot, and can learn more from a folder of your files.</p></div><Button appearance="primary" onClick={() => startOnboarding()}>Start</Button><Button appearance="subtle" onClick={dismissOnboarding}>Not now</Button></section>}{newTopics && <section className="onboarding-card od-row"><span className="getting-started-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>Tell your assistant about your people</h3><p>New questions cover your family and the organizations in your life. Your earlier answers stay as they are.</p></div><Button appearance="primary" onClick={() => startOnboarding('new')}>Start</Button><Button appearance="subtle" onClick={dismissNewTopics}>Not now</Button></section>}
            <div className="bot-grid">{bots.filter(matches).map(b => <BotCard key={b.id} item={b}/>)}</div>{!bots.filter(matches).length && <Empty title={online ? 'No bots found' : 'Harness offline'} text={online ? 'Try a different name or create a new bot.' : 'Bots live in the harness. Start it to see them.'} action={online ? 'Create a bot' : undefined} onClick={() => openForm('bot')}/>}
          </>}
          {page === 'Conversations' && <><PageHeading title="Your conversations" description="Pick up where you left off with a bot, or open a room where several work together." action="Start a conversation" actionIcon={<Chat24Regular/>} onAction={startConversation}/>
            <section><div className="section-heading od-row"><h2>Recent conversations</h2><span className="section-count">{recent.length}</span></div>
              {recent.length > 0 ? <div className="list-panel">{recent.map(c => { const owner = bots.find(b => b.id === c.owner); return <article className="resource-row od-row" key={c.id}>{owner ? <Avatar item={owner} small/> : <span className="resource-icon"><Bot24Regular/></span>}<div className="od-field od-fill"><h3>{conversationTitle(c)}</h3><p>{recentConversationMeta(c, owner?.name)}</p></div><Button appearance="outline" onClick={() => openConversation(c.owner, c.id)}>Open</Button></article>; })}</div>
                : <Empty title={online ? 'No conversations yet' : 'Harness offline'} text={online ? 'Your five most recent conversations with a bot show here.' : 'Conversations live in the harness. Start it to see them.'} action={online && assistant ? 'Start a conversation' : undefined} onClick={startConversation}/>}
            </section>
            <section><div className="section-heading od-row"><h2>Rooms</h2><span className="section-count">{roomGroups.active.length}</span><div className="od-fill"/><Button appearance="outline" icon={<People24Regular/>} disabled={bots.length < 2} onClick={() => openForm('room')}>Start a room</Button></div>
              {rooms.length === 0 && <Empty title="No rooms yet" text={bots.length < 2 ? 'A room needs at least two bots working together. Create another bot first.' : 'A room puts several bots on one topic. ZenBot opens one when a task needs more than one specialist.'}/>}
              {[['', roomGroups.active], ['Archived', roomGroups.archived]].map(([title, list]) => list.length > 0 && <React.Fragment key={title || 'active'}>{title && <div className="section-heading"><h2>{title}</h2></div>}<div className="list-panel">{list.map(r => <article className="resource-row od-row" key={r.id}><span className="resource-icon"><People24Regular/></span><div className="od-field od-fill"><h3>{r.name}{r.lead === ASSISTANT_ID && <Badge className="lead-badge" appearance="tint" color="brand" size="small">Led by ZenBot</Badge>}</h3>{r.topic && <p className="room-topic">{r.topic}</p>}<p>{r.members.map(m => bots.find(b => b.id === m)?.name || m).join(', ')}{r.active_speaker ? ` · speaking: ${bots.find(b => b.id === r.active_speaker)?.name || r.active_speaker}` : ''}</p></div><Button appearance="outline" onClick={() => { setRoomId(r.id); setRoom(r); setMode('room'); navigate('Conversation'); }}>Open room</Button><Button appearance="subtle" onClick={() => archiveRoom(r, !r.archived)}>{r.archived ? 'Unarchive' : 'Archive'}</Button><Button appearance="subtle" onClick={() => deleteRoom(r)}>Delete</Button></article>)}</div></React.Fragment>)}
            </section>
          </>}
          {page === 'Conversation' && mode === 'room' && room && <div className="conversation-layout"><section className="chat-panel"><header className="chat-heading od-row"><span className="resource-icon"><People24Regular/></span><div className="od-field od-fill"><h2>{room.name}</h2><span>{room.members.length} members{room.topic ? ` · ${room.topic}` : ''} · bots can use their tools here, and files they make show up under their reply</span></div><ConversationPicker current={room.conversation_id} conversations={room.conversations} onNew={() => switchRoomConversation(null)} onSelect={switchRoomConversation} disabled={!online || !!room.open_turn}/><Button appearance="subtle" onClick={() => { setMode('bot'); }}>Back to bots</Button></header><div className="messages" aria-live="polite">{!room.posts.length && <div className="chat-welcome"><h1>Start the conversation</h1><p>Mention a bot with @name or post to the room; the floor goes to one bot at a time.</p></div>}{room.posts.map(p => <article className={`message ${p.author === 'user' ? 'user' : 'assistant'}`} key={p.id}><div className="message-byline">{p.author === 'user' ? 'You' : bots.find(b => b.id === p.author)?.name || p.author}<span>{p.at?.slice(11, 16)}</span></div>{p.author === 'user' ? <div className="message-content">{p.text}</div> : <Markdown text={p.text} attachments={(p.attachments || []).map(att => attachmentItem(att, api.attachmentUrl(room.id, att.id)))}/>}</article>)}<div ref={endRef}/></div><form className="composer" onSubmit={e => { e.preventDefault(); postToRoom(composer); }}><label htmlFor="task-input">Post to {room.name}</label><Textarea id="task-input" value={composer} onChange={(e, v) => setComposer(v.value)} onKeyDown={submitOnEnter} placeholder="Say something to the room…" resize="vertical" rows={2}/><div className="composer-bottom od-row"><span>{room.active_speaker ? `${bots.find(b => b.id === room.active_speaker)?.name || room.active_speaker} has the floor` : 'Floor is open'}</span><div className="od-fill"/><Button appearance="primary" type="submit" icon={<ArrowUp24Regular/>} disabled={!composer.trim() || !bound}>Post</Button></div></form></section><aside className="task-panel activity-side"><header className="feed-header od-row"><h3>{roomSpeakerId ? `${bots.find(b => b.id === roomSpeakerId)?.name || roomSpeakerId}'s activity` : 'Activity'}</h3>{room.open_turn && <span className="live-pill" role="status"><span className="live-dot" aria-hidden="true"/>Live</span>}</header><ActivityFeed rows={activityFeed(trail.sessionId === roomSession ? trail.events : [])} live={!!room.open_turn} task={roomSession}/></aside></div>}
          {page === 'Conversation' && mode === 'bot' && bot && <div className="conversation-layout"><section className="chat-panel"><header className="chat-heading od-row"><Avatar item={bot} small/><div className="od-field od-fill"><h2>{bot.name}</h2><span>{bot.skills.length} skills{bot.model ? ` · ${modelName(providers, bot.model)}` : ''} · {statusLabel}</span></div><ConversationPicker current={conv?.botId === bot.id ? conv.current : null} conversations={conv?.botId === bot.id ? conv.conversations : []} onNew={() => switchConversation(bot.id, null)} onSelect={id => switchConversation(bot.id, id)} disabled={!online || live?.botId === bot.id}/><Button appearance="subtle" onClick={() => openForm('bot', { ...bot, color: colorOf(bot) })}>Edit bot</Button></header><div className="messages" aria-live="polite">{interviewing && interview.messages.map((m, i) => <article className={`message ${m.author === 'user' ? 'user' : 'assistant'}`} key={i}><div className="message-byline">{m.author === 'user' ? 'You' : bot.name}</div>{m.author === 'user' ? <div className="message-content">{m.text}</div> : <Markdown text={m.text}/>}{m.facts?.length > 0 && <div className="fact-chips">{m.facts.map(f => <Badge key={f.id} appearance="tint" color="brand" size="medium" icon={<Sparkle24Regular/>}>Remembered: {short(f.text, 90)}</Badge>)}</div>}</article>)}{interviewing && interview.done && <div className="inline-note od-stack"><Checkmark24Regular/><span>That's the interview. Import a file or a folder to let your assistant learn from your files, or go back to tasks.</span><div className="od-row"><Button appearance="outline" onClick={() => setInterview(null)}>Done, back to tasks</Button></div></div>}{!interviewing && !exchanges.length && <div className="chat-welcome"><Avatar item={bot}/><h1>What shall we work on?</h1><p>{bot.role}</p><div className="suggestions">{suggestions.map(s => <Button key={s} appearance="outline" onClick={() => setComposer(s)}>{s}<ArrowRight24Regular/></Button>)}</div></div>}{!interviewing && exchanges.map((ex, i) => { const s = sessions.find(x => x.id === ex.session_id); const pendingNow = ex.outcome === 'pending'; const streaming = pendingNow && live?.sessionId === ex.session_id; const waiting = pendingNow && ['paused', 'waiting_approval'].includes(s?.state); return <React.Fragment key={`${i}-${ex.session_id || ''}`}><article className="message user"><div className="message-byline">You<span>{ex.at?.slice(11, 16)}</span></div><div className="message-content">{ex.user}</div></article>{!pendingNow && <article className="message assistant"><div className="message-byline">{bot.name}<span><Badge appearance="tint" color={outcomeColor(ex.outcome)} size="small">{ex.outcome}</Badge></span></div>{ex.reply || ex.attachments?.length ? <Markdown text={ex.reply} attachments={(ex.attachments || []).map(att => attachmentItem(att, api.conversationAttachmentUrl(conv.record.id, att.id)))}/> : emptyReplyNote(ex) && <div className="message-content">{emptyReplyNote(ex)}</div>}{ex.outcome === 'done' && ex.reply && <Button appearance="subtle" icon={<Sparkle24Regular/>} onClick={() => rememberResult({ id: ex.session_id, result: ex.reply })}>Remember this</Button>}</article>}{waiting && <article className="message assistant"><div className="message-byline">{bot.name}<span><Badge appearance="tint" color={stateColor(s.state)} size="small">{s.state.replace('_', ' ')}</Badge></span></div><div className="message-content">{s.state === 'paused' ? s.pause_reason : 'Waiting for your approval.'}</div></article>}{(streaming || (pendingNow && s?.state === 'running')) && <div className="working-state od-row"><Spinner size="tiny"/><span>{!streaming ? 'Working' : live.events.at(-1)?.kind === 'tool_call' ? `Tool: ${live.events.at(-1).data?.tool || ''}` : live.events.at(-1)?.kind === 'turn' ? 'Thinking…' : live.events.at(-1)?.kind === 'pause' ? 'Paused' : 'Working'}…</span></div>}</React.Fragment>; })}{scopePrompt?.botId === bot.id && <div className="inline-note od-stack"><ShieldCheckmark24Regular/><span>This looks like a change of {scopePrompt.change.new_goal ? 'goal' : 'constraints'} (confidence {Number(scopePrompt.change.probability).toFixed(2)}). Confirm to make “{short(scopePrompt.text, 80)}” the protected task, or reject to treat it as a refinement.</span><div className="od-row"><Button appearance="primary" onClick={() => resolveScope(true)}>Confirm scope change</Button><Button appearance="outline" onClick={() => resolveScope(false)}>Reject, just refine</Button></div></div>}{importJob && bot.id === ASSISTANT_ID && <article className="message assistant"><div className="message-byline">{bot.name}<span><Badge appearance="tint" color={importJob.state === 'running' ? 'brand' : importJob.state === 'done' ? 'success' : 'warning'} size="small">{importJob.state === 'running' ? 'importing' : importJob.state === 'stopping' ? 'stopping…' : importJob.state}</Badge></span></div><div className="message-content">{importJob.state === 'running' ? `${importJob.done} / ${importJob.total} · ${importJob.current ? importJob.current.slice(importJob.dir.length + 1) : 'starting'}` : importJob.state === 'stopping' ? `Stopping… ${importJob.done} / ${importJob.total} done. A file already being saved finishes first.` : `${importJob.state === 'stopped' ? 'Stopped. ' : ''}Imported ${importJob.imported} ${importJob.imported === 1 ? 'file' : 'files'}, ${importJob.unchanged} unchanged, ${importJob.refused} refused.`}</div>{importJob.state === 'running' || importJob.state === 'stopping' ? <Button appearance="subtle" icon={<Stop24Regular/>} disabled={importJob.state === 'stopping'} onClick={stopImport}>{importJob.state === 'stopping' ? 'Stopping…' : 'Stop import'}</Button> : <Button appearance="subtle" icon={<People24Regular/>} onClick={() => navigate('Knowledge')}>Open Knowledge</Button>}</article>}{progressNote && bot.id === ASSISTANT_ID && <div className="inline-note" role="status">{progressNote}</div>}{busy && <div className="working-state od-row"><Spinner size="tiny"/><span>Thinking…</span></div>}{routing && <div className="working-state od-row" role="status"><Spinner size="tiny"/><span>Finding the right bot…</span></div>}<div ref={endRef}/></div><form className="composer" onSubmit={e => { e.preventDefault(); send(composer); }}><label htmlFor="task-input">Message {bot.name}</label><Textarea id="task-input" value={composer} onChange={(e, v) => setComposer(v.value)} onKeyDown={submitOnEnter} placeholder="Give your bot a clear task…" resize="vertical" rows={2}/><div className="composer-bottom od-row"><span>{bound ? 'Sends, publishes, and writes outside allowed folders wait for your approval' : 'Bind a provider in Settings to send'}</span><div className="od-fill"/>{bot.id === ASSISTANT_ID && !canPick && <Input className="dir-field" value={dirField} onChange={(e, v) => setDirField(v.value)} placeholder="Folder or file path to import" aria-label="Folder or file path to import"/>}{bot.id === ASSISTANT_ID && <Button appearance="outline" icon={<Document24Regular/>} disabled={!online || !bound || importJob?.state === 'running' || importJob?.state === 'stopping'} onClick={pickFile}>Import a file</Button>}{bot.id === ASSISTANT_ID && <Button appearance="outline" icon={<Folder24Regular/>} disabled={!online || !bound || importJob?.state === 'running' || importJob?.state === 'stopping'} onClick={pickFolder}>Import a folder</Button>}{bot.id === ASSISTANT_ID && !interviewing && <Button appearance={newTopic ? 'primary' : 'outline'} aria-pressed={newTopic} icon={<Add24Regular/>} title="The next message starts a new room instead of continuing a matching one" onClick={() => setNewTopic(v => !v)}>New topic</Button>}{live?.botId === bot.id ? <Button icon={<Stop24Regular/>} onClick={stopTask}>Stop task</Button> : <Button appearance="primary" type="submit" icon={<ArrowUp24Regular/>} disabled={!composer.trim() || !online || routing}>Send</Button>}</div></form></section><aside className="task-panel activity-side"><header className="feed-header od-row"><h3>Activity</h3>{live?.botId === bot.id ? <span className="live-pill" role="status"><span className="live-dot" aria-hidden="true"/>Live</span> : latest && <Badge appearance="tint" color={stateColor(latest.state)} size="small">{latest.state.replace('_', ' ')}</Badge>}<div className="od-fill"/>{latest && <span className="feed-steps">{latest.steps} {latest.steps === 1 ? 'step' : 'steps'}</span>}</header>{latest && <SessionStatus s={latest}/>}<ActivityFeed rows={activityFeed(live?.botId === bot.id ? live.events : trail.sessionId === latest?.id ? trail.events : [])} live={live?.botId === bot.id} task={latest?.task}/>{latest && <details className="feed-routing"><summary>Skills and protected task</summary><SessionPanel s={latest}/></details>}</aside></div>}
          {page === 'Music' && <MusicArea key={musicKey} api={api} status={musicStatus} refreshStatus={refreshMusic} notify={setNotice}/>}
          {page === 'Skills' && <><PageHeading title="Good work, worth repeating." description="Skills live in the harness catalog. Teach one by recording a task, then let the decider suggest which bot should carry it."/><Search query={query} setQuery={setQuery} label="Search skills"/>
            {fits.filter(f => f.state === 'pending').length > 0 && <><div className="section-heading"><h2>Fit suggestions</h2></div><div className="list-panel">{fits.filter(f => f.state === 'pending').map(f => <article className="resource-row od-row-top" key={f.id}><span className="resource-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>{f.skill}</h3><p>{f.bot_ids.length ? `Suggested: ${f.bot_ids.map(id => `${bots.find(b => b.id === id)?.name || id} (${Number(f.role_scores?.[id] ?? 0).toFixed(2)})`).join(', ')}` : 'No bot scored high enough. Choose one yourself.'}</p></div><div className="approval-actions"><Button appearance="primary" onClick={() => { setFitChoice(c => ({ ...c, [f.id]: (c[f.id] ?? f.bot_ids[0]) || '' })); setActiveFitId(f.id); }}>Review</Button></div></article>)}</div></>}
            <div className="list-panel">{skills.filter(s => s.kind !== 'mcp').filter(matches).map(s => <article className="resource-row od-row-top" key={s.ref}><span className="resource-icon"><Sparkle24Regular/></span><div className="od-field od-fill"><h3>{s.name} <Badge appearance="outline" size="small">{s.kind}</Badge></h3><p>{s.use}</p><p className="quiet-label">Bots with this skill: {bots.filter(b => b.skills.includes(s.ref)).map(b => b.name).join(', ') || 'no bot yet'}</p><details><summary>Frontmatter</summary><dl className="full-text"><div><dt>ref</dt><dd>{s.ref}</dd></div>{s.inputs?.length > 0 && <div><dt>inputs</dt><dd>{s.inputs.map(i => typeof i === 'string' ? i : Object.entries(i).map(([k, v]) => `${k}: ${v}`).join('; ')).join(', ')}</dd></div>}{s.needs?.length > 0 && <div><dt>needs</dt><dd>{s.needs.join(', ')}</dd></div>}{s.limits?.length > 0 && <div><dt>limits</dt><dd>{s.limits.join(', ')}</dd></div>}{s.band_width != null && <div><dt>band width</dt><dd>{s.band_width}</dd></div>}</dl></details></div><Button appearance="outline" disabled={!harness.settings?.scorer || !bots.length} onClick={() => suggestFit(s)}>Suggest a fit</Button></article>)}</div>{!skills.filter(s => s.kind !== 'mcp').filter(matches).length && <Empty title={online ? 'No skills found' : 'Harness offline'} text={online ? 'Teach a task from the Bots page to draft a skill, or add one under the skills directory.' : 'Skills come from the harness catalog.'}/>}
          </>}
          {page === 'MCP servers' && <><PageHeading title="Tools your bots can reach." description="MCP servers listed in mcp.json connect when the harness starts. Each line below is what the decider reads when it chooses which servers a task gets. A server that needs an API key asks for it on its card; a key saved there wins over .env and reconnects the server at once. Toolkits built into the harness, like image generation, are managed in Settings." action="Manage integrated toolkits" actionIcon={<Settings24Regular/>} actionAppearance="outline" onAction={() => { navigate('Settings'); setSettingsTab('tools'); }}/><Search query={query} setQuery={setQuery} label="Search MCP servers"/>
            <div className="list-panel">{Object.entries(mcps).map(([name, m]) => ({ name, ...m })).filter(matches).map(m => <article className="resource-row od-row-top" key={m.name}><span className="resource-icon"><PlugConnected24Regular/></span><div className="od-field od-fill"><h3>{m.name} <Badge appearance="tint" size="small" color={m.state === 'connected' ? 'success' : m.state === 'failed' ? 'danger' : m.state === 'needs_install' ? 'warning' : 'subtle'}>{m.state}</Badge></h3><p>{m.use || 'No description.'}</p><span className="quiet-label">{m.transport} · {m.target}</span>{m.error && <p className="field-hint">{m.error}</p>}<McpDeps server={m} ctx={settingsCtx} onSaved={setMcps}/><McpKeys server={m} ctx={settingsCtx} onSaved={setMcps}/><details><summary>{m.tools.length} {m.tools.length === 1 ? 'tool' : 'tools'}</summary><dl className="full-text">{m.tools.map(t => <div key={t}><dt>{m.name}.{t}</dt></div>)}</dl></details></div></article>)}</div>{!Object.keys(mcps).length && <Empty title={online ? 'No MCP servers configured' : 'Harness offline'} text={online ? 'Add one to mcp.json at the repo root and restart the harness.' : 'MCP servers are listed by the harness.'}/>}
          </>}
          {page === 'Routines' && <><PageHeading title="Put good work on repeat." description="Pair a bot with a skill. Set a schedule. Keep the work moving." action="Create a routine" onAction={() => setRoutineDialog({})}/>{routineStore?.errors?.length > 0 && <div className="form-error" role="alert">{routineStore.read_only ? 'Routines cannot be saved right now. ' : 'Some routines could not be read and are not shown. '}{routineStore.errors.join(' ')}</div>}{importNote && importNotice(importNote) && <div className="inline-note" role="status"><Clock24Regular/><span>{importNotice(importNote)} <button className="text-link" onClick={() => { if (importNote.unparsed.length) { try { dropLegacyRoutines(localStorage, STORAGE); } catch { /* storage unavailable */ } } setImportNote(null); }}>{importNote.unparsed.length ? 'Dismiss for good' : 'Dismiss'}</button></span></div>}<div className="list-panel">{routines.length === 0 && <Empty title="No routines yet" text="Create one to run a skill on a schedule."/>}{routines.map(r => <article className="resource-row od-row-top" key={r.id}><span className="resource-icon"><Clock24Regular/></span><div className="od-field od-fill"><h3>{r.name}{r.managed_by && <> <Badge appearance="tint">Managed by {r.managed_by}</Badge></>}</h3><p>{scheduleLabel(r)}</p><span>{bots.find(b => b.id === r.bot)?.name || r.bot} · {skills.find(s => s.ref === r.skill)?.name || r.skill}</span><span>Next run: {r.error ? 'none' : !r.enabled ? 'paused' : formatWhen(r.next_run) || 'none'} · Last run: {r.last_run ? `${formatWhen(r.last_run.at)}, ${describeOutcome(r.last_run.outcome, r.last_run.reason)}` : 'never'}{r.last_skip && ` · Last skip: ${formatWhen(r.last_skip.at)}, ${describeOutcome(r.last_skip.outcome, r.last_skip.reason)}`}</span>{r.error && <span className="form-error" role="alert">This schedule is not valid ({r.error}). Edit it to fix it, or delete the routine.</span>}</div><div className="routine-controls"><Switch checked={r.enabled} label={r.enabled ? 'Enabled' : 'Paused'} onChange={(e, v) => toggleRoutine(r, v.checked)}/><Button appearance="outline" onClick={() => setRoutineDialog({ routine: r })}>Edit</Button><Button appearance="outline" onClick={() => deleteRoutine(r)}>Delete</Button><Button appearance="primary" icon={<Play24Regular/>} disabled={!online || routineBusy === r.id} onClick={() => runRoutine(r)}>Run now</Button></div></article>)}</div><div className="section-heading"><h2>Run history</h2></div>{sessions.length ? <div className="list-panel">{sortByTime(sessions).reverse().slice(0, 20).map(s => <div className="resource-row od-row" key={s.id}><span className="resource-icon"><Document24Regular/></span><div className="od-field od-fill"><strong>{short(s.task, 90)}</strong><span>{bots.find(b => b.id === s.bot_id)?.name || s.bot_id} · {s.created_at?.replace('T', ' ').slice(0, 16)} · {s.steps} steps</span></div><Badge appearance="tint" color={stateColor(s.state)}>{s.state.replace('_', ' ')}</Badge><Button appearance="subtle" onClick={() => openBot(s.bot_id)}>View</Button></div>)}</div> : <Empty title="A fresh start" text="Run a task or routine to see its history here."/>}</>}
          {page === 'Connected apps' && <><PageHeading title="Bring your tools together." description="App connections for the services your bots will reach. Tool servers live on the MCP servers page."/><div className="section-heading"><h2>Apps</h2></div><div className="inline-note"><PlugConnected24Regular/><span>App connections are placeholders until connectors land in the harness. No sign-in, credentials, or access to your accounts is requested.</span></div><div className="apps-grid">{appOptions.map(([name, description, initials]) => { const connected = data.connections.includes(name); return <article className="app-card od-stack" key={name}><div className="od-row"><span className="app-lettermark">{initials}</span><div className="od-fill"/>{connected && <Badge color="success" appearance="tint">Connected</Badge>}</div><h2>{name}</h2><p>{description}</p><Button appearance={connected ? 'outline' : 'primary'} onClick={() => { if (connected) { setModal('disconnect'); setForm({ name }); } else { update(d => ({ ...d, connections: [...d.connections, name], activity: addActivity(d, `${name} marked connected`, 'Placeholder; no account access.') })); setNotice(`${name} marked as connected`); } }}>{connected ? 'Disconnect' : 'Connect app'}</Button></article>; })}</div></>}
          {page === 'Approvals' && <><PageHeading title="The final say is yours." description="Review what your bots want to do before the next step happens."/><div className="inline-note"><ShieldCheckmark24Regular/><span>A bot pauses on a send, publish, or payment tool call, and on a shell command that may write outside the folders it may use, until you decide. Every decision is in the ledger.</span></div>{!approvals.length ? <Empty title="You're all caught up" text="Ask a bot to send or publish something to see the approval flow." action={bots[0] ? `Open ${bots[0].name}` : undefined} onClick={() => { openBot(bots[0].id); setComposer('Draft and send a weekly team update'); }}/> : <div className="list-panel">{approvals.map(a => <article className="resource-row od-row-top" key={a.id}><span className="resource-icon"><ShieldCheckmark24Regular/></span><div className="od-field od-fill"><Badge appearance="tint" color={a.state === 'pending' ? 'warning' : a.state === 'approved' ? 'success' : 'subtle'}>{a.state === 'pending' ? 'Needs your review' : a.state}</Badge><h3>{a.title}</h3><p>{a.detail}</p><span className="quiet-label">{a.tool ? `tool ${a.tool}` : ''}{a.rule ? ` · rule ${a.rule}` : ''} · {bots.find(b => b.id === a.bot_id)?.name || a.bot_id}</span><Button appearance="subtle" onClick={() => openBot(a.bot_id)}>View session <ArrowRight24Regular/></Button></div>{a.state === 'pending' && <div className="approval-actions"><Button appearance="outline" onClick={() => decide(a, false)}>Reject</Button><Button appearance="primary" onClick={() => decide(a, true)}>Approve</Button></div>}</article>)}</div>}</>}
          {page === 'Ledger' && <><PageHeading title="Everything, on the record." description="Model calls, tool calls, decider scores, pauses, approvals, memory, and settings changes. Secrets are redacted before they are written."/><div className="od-row"><Field label="Kind"><select value={ledgerKind} onChange={e => setLedgerKind(e.target.value)}><option value="">All kinds</option>{['session', 'turn', 'tool_call', 'decider_score', 'promotion', 'pause', 'resume', 'approval_request', 'approval_decision', 'error', 'compaction', 'scope_change', 'supervisor', 'room', 'room_post', 'room_turn', 'handoff', 'helper', 'recording', 'memory', 'ingest', 'learn', 'settings'].map(k => <option key={k} value={k}>{k}</option>)}</select></Field><div className="od-fill"/><Button appearance="outline" onClick={() => guard(async () => setLedger(await api.ledger({ n: 100, kind: ledgerKind || undefined })))}>Refresh</Button></div>{!ledger.length ? <Empty title="Nothing recorded yet" text={online ? 'Run a task and the ledger fills in.' : 'The ledger lives in the harness.'}/> : <div className="activity-list">{[...ledger].reverse().map(e => <div className="activity-row od-row-top" key={e.id}><span className={`activity-symbol ${e.kind === 'error' ? 'approval' : 'ready'}`}><Checkmark24Regular/></span><div className="od-field od-fill"><strong>{e.kind}{e.data?.event ? ` · ${e.data.event}` : ''}{e.data?.tool ? ` · ${e.data.tool}` : ''}{e.data?.action ? ` · ${e.data.action}` : ''}</strong><span>{short(e.data, 200)}</span><span className="quiet-label">{bots.find(b => b.id === e.bot_id)?.name || e.bot_id || 'harness'}{e.session_id ? ` · session ${e.session_id}` : ''}{e.redacted?.length ? ` · ${e.redacted.length} redacted` : ''}</span></div><span className="activity-time">{e.ts?.replace('T', ' ').slice(5, 19)}</span></div>)}</div>}</>}
          {page === 'Settings' && <><PageHeading title="Your workspace, your way." description="Settings grouped by what they control: the models the harness runs on, the providers they come from, the tools it can hand to bots, the folders bots may read and write, where errors and traces go, your memory, and this app."/><TabList className="settings-tabs" selectedValue={settingsTab} onTabSelect={(e, d) => setSettingsTab(d.value)} aria-label="Settings sections">{[['harness', 'Harness settings'], ['providers', 'Model providers'], ['tools', 'Harness Tools'], ['folders', 'Allowed Folders'], ['tracing', 'Tracing'], ['memory', 'Memory'], ['about', 'About']].map(([v, label]) => <Tab key={v} value={v} id={`settings-tab-${v}`}>{label}</Tab>)}</TabList><div className="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${settingsTab}`}>
            {settingsTab === 'harness' && <HarnessSettingsTab ctx={settingsCtx} providers={providers} roles={modelRoles} onSaved={r => { setModelRoles(r); refreshHealth(); refreshModels(); }} openProviders={() => setSettingsTab('providers')} status={<div className="resource-row od-row"><div className="od-field od-fill"><h3>Harness</h3><p>{api.baseUrl} · {statusLabel}{harness.settings ? ` · scorer ${harness.settings.scorer || 'none'} · memory ${harness.settings.memory || '?'}` : ''}</p></div><Badge appearance="outline" color={online ? 'success' : 'danger'}>{online ? 'Online' : 'Offline'}</Badge></div>}/>}
            {settingsTab === 'providers' && <ModelProvidersTab ctx={settingsCtx} providers={providers} kinds={providerKinds} presets={providerPresets} reload={() => Promise.all([refreshModels(), refreshTools()])}/>}
            {settingsTab === 'tools' && <>{toolSettings === null ? <div className="resource-row"><p>{online ? 'Loading the harness tools…' : 'The harness is offline.'}</p></div> : toolSettings.map(t => { const f = toolForms[t.id] || toolForm(t); const groups = enabledByKind(providers, 'image', t.providers); const cur = parseChoice(f.choice); const missing = cur && !groups.some(g => g.provider === cur.provider && g.models.some(m => m.id === cur.model)); const dirty = f.choice !== choiceKey(t.provider, t.model); return <form key={t.id} className="resource-row editor-form" onSubmit={e => { e.preventDefault(); saveTool(t.id, parseChoice(f.choice), x => `Saved ${x.name}: ${x.model}`); }}><div className="od-row-top tool-heading"><div className="od-field od-fill"><h3>{t.name}</h3><p>{t.summary}</p></div><Switch checked={t.enabled} disabled={!online} label={t.enabled ? 'On' : 'Off'} onChange={(e, d) => saveTool(t.id, { enabled: d.checked }, x => `${x.name} ${x.enabled ? 'on' : 'off'}`)}/></div>{t.enabled && !t.active && <p className="form-error" role="alert">{t.provider} is not a provider that makes images, so bots are not offered this tool. Pick a model below.</p>}<Field label="Image model" hint="Choose from the image models enabled on the Model providers tab. Image generation runs through OpenRouter providers.">{groups.length ? <select aria-label="Image model" value={f.choice} disabled={!online} onChange={e => setToolForms(x => ({ ...x, [t.id]: { choice: e.target.value } }))}>{missing && <option value={f.choice}>{cur.model} (not enabled)</option>}{groups.map(g => <optgroup key={g.provider} label={g.name}>{g.models.map(m => <option key={m.id} value={choiceKey(g.provider, m.id)}>{modelLabel(m)}</option>)}</optgroup>)}</select> : <p className="field-hint">No image models enabled on an OpenRouter provider. <button type="button" className="text-link" onClick={() => setSettingsTab('providers')}>Enable one on the Model providers tab</button>.</p>}</Field><p className="field-hint">Uses the key of the model's provider. Images are saved in the workspace's generated-images folder and attached to the bot's reply. Saved here, this choice wins over .env.</p><div className="od-row"><Badge appearance="tint" color={t.active ? 'success' : 'informative'}>{t.active ? `${t.tool} · offered when the decider picks it` : 'Off · no bot is offered it'}</Badge><div className="od-fill"/>{dirty && <Button appearance="subtle" onClick={() => setToolForms(x => ({ ...x, [t.id]: toolForm(t) }))}>Discard</Button>}<Button appearance="primary" type="submit" disabled={!online || !dirty}>Save</Button></div></form>; })}<ProgramPathSection ctx={settingsCtx} programKey="imagemagick"/></>}
            {settingsTab === 'folders' && <AllowedFoldersTab ctx={settingsCtx}/>}
            {settingsTab === 'tracing' && <TracingSettingsTab ctx={settingsCtx}/>}
            {settingsTab === 'memory' && <>{profile ? <form className="resource-row editor-form" onSubmit={saveOwner}><h3>About you</h3><Field label="Your name" required><Input required value={ownerForm.name} onChange={(e, d) => setOwnerForm(f => ({ ...f, name: d.value }))}/></Field><Field label="Also called" hint="Nicknames, separated by commas"><Input value={ownerForm.also} onChange={(e, d) => setOwnerForm(f => ({ ...f, also: d.value }))}/></Field><Button appearance="primary" type="submit">Save name</Button></form> : <div className="resource-row"><h3>About you</h3><p>Your name and nicknames are kept in the memory store, which this harness is not running.</p></div>}{profile && <div className="resource-row od-row"><div className="od-field od-fill"><h3>Rebuild memory</h3><p>Re-learn everything shared under the current knowledge model: people, organizations, and how they relate. Your facts and each bot's private memory stay.</p>{!profile.owner && <p className="field-hint">Add your name above first: the rebuild re-learns your people under it.</p>}</div><Button appearance="outline" disabled={!online || !bound || !profile.owner} onClick={previewRebuild}>Rebuild memory…</Button></div>}</>}
            {settingsTab === 'about' && <><div className="resource-row"><h3>What this app does</h3><p>Bots, skills, approvals, rooms, memory, and the ledger come from the zen-agents harness on localhost. The connected-apps list and bot colours stay in this browser or Electron profile. Routines are scheduled by the harness.</p></div><div className="resource-row"><h3>Open source</h3><p>Authored source is MIT licensed. See README.md for setup, source locations, and dependency licenses.</p></div></>}</div></>}
        </MediaProvider></main>
      </div>
    </div>
    <Dialog open={!!modal} onOpenChange={(e, v) => { if (!v.open) setModal(null); }}><DialogSurface className="edit-dialog"><DialogBody><DialogTitle action={<Button appearance="subtle" icon={<Dismiss24Regular/>} aria-label="Close dialog" onClick={() => setModal(null)}/>}>{modal === 'disconnect' ? `Disconnect ${form.name}?` : modal === 'teach' ? 'Teach a task' : modal === 'room' ? 'Start a room' : `${form.id ? 'Edit' : 'Create'} a bot`}</DialogTitle><DialogContent>{modal === 'disconnect' ? <p>This removes the placeholder connection from your local workspace.</p> : <form id="editor-form" onSubmit={saveForm} className="editor-form">{Object.values(errors).some(Boolean) && <div role="alert" className="form-error">Complete the highlighted fields below.</div>}<Field label="Name" required validationMessage={errors.name}><Input autoFocus maxLength={64} value={form.name || ''} onChange={(e, v) => setForm(f => ({ ...f, name: v.value }))} onBlur={() => checkField('name', form.name)}/></Field>{modal === 'bot' && <><Field label="Role" required validationMessage={errors.role}><Input value={form.role || ''} onChange={(e, v) => setForm(f => ({ ...f, role: v.value }))} onBlur={() => checkField('role', form.role)} placeholder="One line on what this bot is for"/></Field><Field label="Instructions"><Textarea rows={5} resize="vertical" value={form.instructions || ''} onChange={(e, v) => setForm(f => ({ ...f, instructions: v.value }))}/></Field><Field label="Bot color"><select value={form.color || 'blue'} onChange={e => setForm(f => ({ ...f, color: e.target.value }))}><option value="blue">Blue</option><option value="green">Green</option><option value="violet">Violet</option></select></Field><Field label="Model" hint="The text model this bot's tasks run on. Enable more on Settings > Model providers."><select aria-label="Model" value={form.model || ''} onChange={e => setForm(f => ({ ...f, model: e.target.value }))}><option value="">{defaultModelLabel(providers, modelRoles)}</option>{form.model && !enabledByKind(providers, 'text').some(g => g.models.some(m => choiceKey(g.provider, m.id) === form.model)) && <option value={form.model}>{parseChoice(form.model).model} (not enabled)</option>}{enabledByKind(providers, 'text').map(g => <optgroup key={g.provider} label={g.name}>{g.models.map(m => <option key={m.id} value={choiceKey(g.provider, m.id)}>{modelLabel(m)}</option>)}</optgroup>)}</select></Field></>}{modal === 'teach' && <><Field label="When to use this skill" required validationMessage={errors.use}><Textarea rows={3} resize="vertical" value={form.use || ''} onChange={(e, v) => setForm(f => ({ ...f, use: v.value }))} onBlur={() => checkField('use', form.use)} placeholder="One or two sentences the decider will score against"/></Field><p className="field-hint">The harness opens a driven browser. Work through the task once; each step is recorded by intent, passwords become handles, and the draft lands in the skills directory for review.</p></>}{modal === 'room' && <><Field label="Members"><div className="od-stack">{bots.map(b => <label key={b.id} className="od-row"><input type="checkbox" checked={(form.members || []).includes(b.id)} onChange={e => setForm(f => ({ ...f, members: e.target.checked ? [...(f.members || []), b.id] : (f.members || []).filter(m => m !== b.id) }))}/> {b.name}</label>)}</div></Field><p className="field-hint">Two to six bots. Posts are text only; each answer runs in that bot’s own session.</p></>}</form>}</DialogContent><DialogActions><Button appearance="secondary" onClick={() => setModal(null)}>Cancel</Button>{modal === 'disconnect' ? <Button appearance="primary" onClick={() => { update(d => ({ ...d, connections: d.connections.filter(c => c !== form.name) })); setNotice(`${form.name} disconnected`); setModal(null); }}>Disconnect</Button> : <Button appearance="primary" type="submit" form="editor-form">{modal === 'teach' ? 'Start recording' : modal === 'room' ? 'Create room' : `Save ${modal}`}</Button>}</DialogActions></DialogBody></DialogSurface></Dialog>
    {routineDialog && <RoutineDialog routine={routineDialog.routine || null} bots={bots} skills={skills} onSave={saveRoutine} onClose={() => setRoutineDialog(null)}/>}
    <Dialog open={!!rebuildPrompt} onOpenChange={(e, v) => { if (!v.open) setRebuildPrompt(null); }}><DialogSurface className="edit-dialog"><DialogBody><DialogTitle>Rebuild memory</DialogTitle><DialogContent><p>{rebuildPrompt ? rebuildSummary(rebuildPrompt) : ''}</p></DialogContent><DialogActions><Button appearance="outline" onClick={() => setRebuildPrompt(null)}>Cancel</Button><Button appearance="primary" disabled={!rebuildPrompt?.facts} onClick={confirmRebuild}>Rebuild</Button></DialogActions></DialogBody></DialogSurface></Dialog>
    <Dialog open={!!scanPrompt} onOpenChange={(e, v) => { if (!v.open) setScanPrompt(null); }}><DialogSurface className="edit-dialog"><DialogBody><DialogTitle>Import a folder</DialogTitle><DialogContent><p>{scanPrompt ? scanSummary(scanPrompt) : ''}</p>{scanPrompt?.files > 0 && scanPrompt.extraction_calls != null && <p className="field-hint">{extractionEstimate(scanPrompt)}</p>}{scanPrompt?.by_suffix && <p className="field-hint">{Object.entries(scanPrompt.by_suffix).map(([k, n]) => `${n} ${k}`).join(' · ')}</p>}</DialogContent><DialogActions><Button appearance="outline" onClick={() => setScanPrompt(null)}>Cancel</Button><Button appearance="primary" disabled={!scanPrompt?.files} onClick={confirmImport}>Import</Button></DialogActions></DialogBody></DialogSurface></Dialog>
    <Dialog open={!!activeFit} onOpenChange={(e, v) => { if (!v.open) setActiveFitId(null); }}><DialogSurface className="edit-dialog"><DialogBody><DialogTitle action={<Button appearance="subtle" icon={<Dismiss24Regular/>} aria-label="Close dialog" onClick={() => setActiveFitId(null)}/>}>{activeFit ? (skills.find(s => s.ref === activeFit.skill)?.name || activeFit.skill) : 'Fit suggestion'}</DialogTitle>{activeFit && <><DialogContent><div className="od-stack"><p>{activeFit.bot_ids?.length ? `Suggested: ${activeFit.bot_ids.map(id => `${bots.find(b => b.id === id)?.name || id} (${Number(activeFit.role_scores?.[id] ?? 0).toFixed(2)})`).join(', ')}` : 'No bot scored high enough. Choose one yourself.'}</p><Field label="Attach to"><select value={fitChoice[activeFit.id] ?? (activeFit.bot_ids[0] || bots[0]?.id || '')} onChange={e => setFitChoice(c => ({ ...c, [activeFit.id]: e.target.value }))}>{bots.map(b => <option key={b.id} value={b.id}>{b.name}{activeFit.role_scores?.[b.id] != null ? ` · ${Number(activeFit.role_scores[b.id]).toFixed(2)}` : ''}</option>)}</select></Field></div></DialogContent><DialogActions><Button appearance="outline" onClick={() => settleFit(activeFit, false)}>Deny</Button><Button appearance="primary" onClick={() => settleFit(activeFit, true)}>Confirm</Button></DialogActions></>}</DialogBody></DialogSurface></Dialog>
    {notice && <div className="toast od-row" role="status"><Checkmark24Regular/><span>{notice}</span><Button appearance="subtle" icon={<Dismiss24Regular/>} aria-label="Dismiss notification" onClick={() => setNotice('')}/></div>}
  </FluentProvider>;
}
const FEED_ICONS = { search: Search20Regular, web: Globe20Regular, shell: WindowConsole20Regular, think: BrainCircuit20Regular, reply: ChatSparkle20Regular, tool: Wrench20Regular, start: Play20Regular, done: CheckmarkCircle20Regular, end: Flag20Regular, error: ErrorCircle20Regular, pause: Pause20Regular, approval: ShieldCheckmark20Regular, supervisor: ShieldCheckmark20Regular, compact: ArrowMinimize20Regular, learn: BookOpen20Regular, memory: Database20Regular };
// The bot's session as it happens: searches, page reads, tool calls, thinking, approvals. Follows the
// newest row while the reader is at the bottom; scrolling up to read an older row stops the follow.
function ActivityFeed({ rows, live, task }) {
  const box = useRef(null);
  const pinned = useRef(true);
  useEffect(() => { if (pinned.current && box.current) box.current.scrollTop = box.current.scrollHeight; }, [rows.length, rows.at(-1)?.status]);
  const onScroll = () => { const b = box.current; if (b) pinned.current = b.scrollHeight - b.scrollTop - b.clientHeight < 48; };
  if (!rows.length) return <div className="feed-empty">{live ? <><Spinner size="tiny"/><span>Starting…</span></> : <p>{task ? 'No activity recorded for this task.' : 'Nothing yet. Send a task and every search, page read and tool call shows up here as it happens.'}</p>}</div>;
  return <ol className="feed" ref={box} onScroll={onScroll} aria-live="polite" aria-label="Task activity">{rows.map(r => {
    const Icon = FEED_ICONS[r.icon] || Wrench20Regular;
    const args = r.arguments && Object.keys(r.arguments).length ? JSON.stringify(r.arguments, null, 2) : '';
    const more = args || r.output || r.error || (r.full && r.full.length > (r.detail || '').length);
    return <li className={`feed-row ${r.status}`} key={r.id}>
      <span className="feed-icon">{r.status === 'running' ? <Spinner size="extra-tiny"/> : <Icon/>}</span>
      <div className="feed-body">
        <div className="feed-title"><strong>{r.title}</strong>{r.status === 'running' && <span className="feed-running">running</span>}{r.status === 'waiting' && <span className="feed-waiting">waiting</span>}<span className="feed-time">{r.time}</span></div>
        {r.detail && <div className="feed-detail">{r.detail}</div>}
        {r.error && <div className="feed-error">{r.error}</div>}
        {r.meta && <div className="feed-meta">{r.meta}</div>}
        {more && <details className="feed-more"><summary>{r.output ? 'Result' : 'Details'}</summary>{r.full && r.full.length > (r.detail || '').length && <pre>{r.full}</pre>}{args && <><div className="feed-label">Arguments</div><pre>{args}</pre></>}{r.output && <><div className="feed-label">Output</div><pre>{r.output}</pre></>}</details>}
      </div>
    </li>;
  })}</ol>;
}
function PageHeading({ title, description, action, onAction, actionIcon = <Add24Regular/>, actionAppearance = 'primary' }) { return <section className="page-heading od-row-top"><div className="od-fill"><div className="eyebrow">YOUR OPENZENBOT WORKSPACE</div><h1>{title}</h1><p>{description}</p></div>{action && <Button appearance={actionAppearance} icon={actionIcon} onClick={onAction}>{action}</Button>}</section>; }
function Search({ query, setQuery, label }) { return <div className="search-field"><Field label={label}><Input contentBefore={<Search24Regular/>} value={query} onChange={(e, v) => setQuery(v.value)} placeholder="Search by name…"/></Field></div>; }
startTelemetry({ harness: harnessUrl() });
createRoot(document.getElementById('root')).render(<App/>);
