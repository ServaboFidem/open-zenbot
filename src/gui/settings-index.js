// The Settings home screen: its panels, what each panel's sections say, and the search over both. Pure, so it is
// tested without the page (tests/gui/settings-index.test.mjs). A new settings section adds an entry to SECTIONS and
// puts id={anchorId(panel, title)} on its element; a section headed by data (a provider, a tool, an agent card)
// has no entry and reaches the search as `extra` instead.

import { APP_STEPS } from './calendar-settings.js';
import { PRIVACY } from './dream.js';
import { FACEBOOK_LOGIN_LOSS } from './discovery-settings.js';
import { MAIL_LINE } from './mail.js';
import { HELP_LINE as SONOS_LINE } from './sonos.js';
import { NETWORK_LINE as SPEAKERS_LINE } from './wifi-speakers.js';

export const PANELS = [
  { id: 'harness', name: 'Harness settings', description: 'The models your bots, summaries and the decider run on.' },
  { id: 'providers', name: 'Model providers', description: 'Where models come from: providers, their API keys and which models are enabled.' },
  { id: 'tools', name: 'Harness Tools', description: 'Toolkits built into the harness: image generation and ImageMagick.' },
  { id: 'mcps', name: 'MCP servers', description: 'Tools your bots can reach: the servers in mcp.json, their API keys and what they need installed.' },
  { id: 'skills', name: 'Skills', description: 'Good work, worth repeating: the skills in the catalog and which bots carry them.' },
  { id: 'agents', name: 'Coding agents', description: 'Claude Code and Codex on this computer: found, signed in, and whether bots may hand work to them.' },
  { id: 'folders', name: 'Allowed Folders', description: 'The folders outside the vault that bots may read or write.' },
  { id: 'calendar', name: 'Calendar', description: 'Google Calendar: your own Google app, which calendars to bring in, and sending events made here to Google.' },
  { id: 'tracing', name: 'Tracing', description: 'Where errors and traces go: Sentry, GlitchTip and Langfuse.' },
  { id: 'ledger', name: 'Ledger', description: 'Everything on the record: model calls, tool calls, decider scores, approvals, memory and settings changes.' },
  { id: 'memory', name: 'Memory', description: 'Your name in memory, and rebuilding what the bots have learned.' },
  { id: 'apps', name: 'Connected apps', description: "AgentMail, ZenBot's own email address, and Sonos speakers; other app connections are a mockup for now." },
  { id: 'speakers', name: 'Wi-Fi speakers', description: 'AirPlay, Chromecast and DLNA speakers on your network, and the default speaker ZenBot will use.' },
  { id: 'about', name: 'About', description: 'What this app does, and its license.' },
];

export function anchorId(panel, title) {
  return `settings-${panel}-${String(title).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')}`;
}

const section = (panel, title, words) => ({ panel, anchor: anchorId(panel, title), title, words });
// A panel whose sections are all headed by data: found by its words, opened at the top.
const whole = (panel, words) => ({ panel, anchor: '', title: PANELS.find(p => p.id === panel).name, words });

export const SECTIONS = [
  section('harness', 'Harness', ['Harness address and status: online or offline', 'Scorer', 'Memory']),
  section('harness', 'Models the harness runs on', [
    'Choose from the models enabled on the Model providers page. Saving switches the harness over at once; running tasks finish on the model they started with.',
    "Chat model: runs your bots' tasks.",
    'Summary model: compacts long tasks, folds old conversation turns, and summarizes imported documents.',
    'Decider scorer: chooses skills and MCP servers per task, and watches long tasks. Without one, every skill and tool is offered.',
    "Decision model: the scorer's model. Decision models come from OpenRouter.",
  ]),
  section('harness', 'Dreaming', [
    'Learn from my conversations while the computer is idle',
    PRIVACY,
    'Dreaming model', 'Start after', 'minutes idle', 'Dream now', 'What was learned', 'Forget', 'learn', 'memory', 'idle', 'conversations', 'local model',
  ]),
  section('providers', 'Model providers', ["Each provider's key lives in the harness's providers.json, readable by you only. Enable the models the harness may use; the Harness settings and Harness Tools pages pick from them.", 'Test a provider', 'Refresh the model list', 'OpenRouter', 'OpenAI-compatible']),
  section('providers', 'Add a provider', [
    'Preconfigured provider', 'Hosted', 'Runs on your machine', 'Name', 'Kind', 'Provider kind',
    "Base URL: where the API's /chat/completions and /models live. Plain http works for this machine, your local network or a Tailscale peer.",
    'API key: saved in providers.json (owner-only). Never sent back to this app.',
    'Key reference: a reference like env:GROQ_API_KEY: the value stays in .env or your shell.',
    'Where the key comes from',
  ]),
  whole('tools', [
    'Image generation: image model. Image generation runs through OpenRouter providers.',
    "Images are saved in the workspace's generated-images folder and attached to the bot's reply.",
    'ImageMagick: the full path of the magick program the harness runs.', 'Program path',
  ]),
  whole('mcps', [
    'MCP servers listed in mcp.json connect when the harness starts. Each line is what the decider reads when it chooses which servers a task gets.',
    'A server that needs an API key asks for it on its card; a key saved there wins over .env and reconnects the server at once.',
    'API key', 'Recheck', 'needs install', 'connected', 'failed', 'tools',
  ]),
  whole('skills', [
    'Skills live in the harness catalog. Teach one by recording a task, then let the decider suggest which bot should carry it.',
    'Fit suggestions', 'Suggest a fit', 'Bots with this skill', 'Frontmatter',
  ]),
  section('agents', 'Coding agents', [
    'Let bots hand a coding job to a program on this computer. The program works in one folder you allow, and you see what it changed.',
    'Claude Code', 'Codex', 'Detected command-line programs (CLIs): where each is, its version and whether it is signed in.',
    'Bots may hand work to it', 'Ask me before each run', "Model: leave empty to use the program's own default.",
    'Program path', 'Runs use your sign-in and send the job folder\'s contents to the vendor.', 'delegate',
  ]),
  section('folders', 'Allowed Folders', ['Folders your bots can reach through the files tool, besides the vault. With Full Access a bot can read, change, or delete anything inside the folder; with Read-Only it can only read, list, and search.', 'A Read-Only folder can sit inside a Full Access one to protect that corner of it. Changes reconnect the files tool right away.', 'Remove a folder']),
  section('folders', 'Add a folder', ['Access for the new folder', 'Folder path: the full path, like D:\\Projects\\site or /home/you/notes.']),
  section('calendar', 'Your Google app', [
    ...APP_STEPS, 'Redirect address', 'Client ID', 'Client secret',
  ]),
  section('calendar', 'Google Calendar', ['Connect Google Calendar', 'Reconnect', 'Disconnect', 'OpenZenBot asks to see your calendars and their events. It does not ask to change anything yet.', 'Google sign-in has expired.']),
  section('calendar', 'Calendars to bring in', ['Choose which of your Google calendars show their events here.', 'view only']),
  section('calendar', 'Send events to Google', ['Send events made here to Google', 'Destination calendar', 'Allow sending to Google']),
  section('calendar', 'Event discovery', [
    'Finds events near you on Eventbrite, once a week, and offers the best ones under Your Recommendations. Nothing is added to your calendar until you say so.',
    'Look for events near me', 'City', 'State', 'Your city’s Eventbrite page', 'Kinds of events', 'Your interests', 'Eventbrite', 'Discover', 'Your Recommendations', 'All Events',
    'FindLocal', 'FindLocal key', 'FindLocal city',
  ]),
  section('calendar', 'Facebook helper', [
    'The browser helper watches your Facebook events, invites and birthdays on this computer and hands what it sees to OpenZenBot.',
    'Invites and events from friends wait under the Facebook tab on the Events page until you approve them. Nothing is added to your calendar until you say so.',
    'Birthdays get a nudge in both chat and feed a week before.',
    'Facebook', 'Facebook helper', 'Chrome helper', 'Events page', 'Facebook tab', 'invites', 'birthdays', 'nudge',
  ]),
  section('calendar', 'Facebook sign-in', [
    FACEBOOK_LOGIN_LOSS,
    'Facebook sign-in', 'signed out', 'login', 'sign back in', 'helper is not ready',
  ]),
  section('tracing', 'Tracing', ['Where the harness sends errors and traces. Saved here, a setting wins over .env and is kept in vault-settings/tracing.json.']),
  section('tracing', 'Sentry (GlitchTip)', [
    'Errors and request traces from the harness and the desktop app.',
    "DSN: from your GlitchTip or Sentry project's settings. Leave empty and save to turn Sentry off.",
  ]),
  section('tracing', 'Langfuse', [
    'Every agent run, model call and tool call, with thinking, tokens and cost.',
    'Public key: Settings › API Keys in your Langfuse project.', 'Secret key',
    'Server: your Langfuse URL. Empty means Langfuse Cloud (EU).',
    'Environment: keeps test traces apart from real ones, e.g. production or development.',
  ]),
  whole('ledger', [
    'Model calls, tool calls, decider scores, pauses, approvals, memory, and settings changes. Secrets are redacted before they are written.',
    'Kind', 'All kinds', 'Refresh', 'log', 'history', 'activity', 'audit', 'record',
  ]),
  section('memory', 'About you', ['Your name', 'Also called: nicknames, separated by commas', 'Owner']),
  section('memory', 'Rebuild memory', ["Re-learn everything shared under the current knowledge model: people, organizations, and how they relate. Your facts and each bot's private memory stay."]),
  section('apps', 'AgentMail', ['AgentMail', 'email', 'inbox', 'address', 'API key', 'newsletter', MAIL_LINE]),
  section('apps', 'Sonos', ['Sonos', 'speakers', 'play music', 'Favorites', 'playlists', 'volume', 'Highest volume ZenBot may set', 'Look again', SONOS_LINE]),
  section('speakers', 'Wi-Fi speakers', ['AirPlay', 'Chromecast', 'DLNA', 'speakers', 'default speaker', 'No default', 'Look again', SPEAKERS_LINE]),
  whole('apps', ["Mockup: connected apps don't work yet. Connect only marks an app as connected in this window.", 'Connect app', 'Disconnect']),
  section('about', 'Version and updates', ['Version', 'Check for updates', 'Check for updates automatically', 'Update to the newest release, or skip a version.']),
  section('about', 'What this app does', ['Bots, skills, approvals, rooms, memory, and the ledger come from the zen-agents harness on localhost.']),
  section('about', 'Open source', ['Authored source is MIT licensed. See README.md for setup, source locations, and dependency licenses.', 'License']),
];

// The sections headed by data, for the search: each provider, harness tool, MCP server and skill, with the id its
// card carries. `mcps` is GET /mcps (an object by server name); `skills` is the catalog list (MCP entries left out).
export function liveSections(providers, tools, mcps, skills) {
  const named = x => !!x && typeof x.name === 'string' && !!x.name && x.id != null;
  return [
    ...(providers || []).filter(named).map(p => ({ panel: 'providers', anchor: anchorId('providers', p.id), title: p.name,
      words: (p.models || []).flatMap(m => [m?.id, m?.name]).filter(w => typeof w === 'string') })),
    ...(tools || []).filter(named).map(t => ({ panel: 'tools', anchor: anchorId('tools', t.id), title: t.name, words: [t.summary].filter(w => typeof w === 'string') })),
    ...Object.entries(mcps && typeof mcps === 'object' && !Array.isArray(mcps) ? mcps : {}).filter(([name, m]) => !!name && !!m && typeof m === 'object')
      .map(([name, m]) => ({ panel: 'mcps', anchor: anchorId('mcps', name), title: name, words: [m.use, ...(Array.isArray(m.tools) ? m.tools : [])].filter(w => typeof w === 'string') })),
    ...(Array.isArray(skills) ? skills : []).filter(s => !!s && s.kind !== 'mcp' && typeof s.name === 'string' && !!s.name && typeof s.ref === 'string' && !!s.ref)
      .map(s => ({ panel: 'skills', anchor: anchorId('skills', s.ref), title: s.name, words: [s.use].filter(w => typeof w === 'string') })),
  ];
}

const MAX_RESULTS = 20;
const panelOf = id => PANELS.find(p => p.id === id);
const usable = s => !!s && typeof s.title === 'string' && !!panelOf(s.panel);

// Matches for what the user typed, best first. `extra` is more sections in the same shape, built from live data.
// A panel is matched by its own name and description; a section only by its own title and words, so a result
// always names the place that holds the words.
export function searchSettings(query, extra = []) {
  const terms = String(query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const wholes = new Map(SECTIONS.filter(s => !s.anchor).map(s => [s.panel, s.words]));
  const entries = [
    ...PANELS.map(p => ({ panel: p.id, anchor: '', title: p.name, words: [p.description, ...(wholes.get(p.id) || [])], whole: true })),
    ...SECTIONS.filter(s => s.anchor),
    ...(Array.isArray(extra) ? extra : []).filter(usable),
  ];
  const found = [];
  for (const s of entries) {
    const panel = panelOf(s.panel);
    const words = (Array.isArray(s.words) ? s.words : []).filter(w => typeof w === 'string');
    const title = s.title.toLowerCase();
    const all = [title, ...words].join('\n').toLowerCase();
    if (!terms.every(t => all.includes(t))) continue;
    const named = terms.every(t => title.includes(t));
    const rank = named ? (s.whole ? 0 : 1) : s.whole ? 3 : 2;   // a section that holds the words comes before a panel that only mentions them
    const text = named ? s.title : words.find(w => terms.some(t => w.toLowerCase().includes(t))) || s.title;
    found.push({ rank, whole: !!s.whole, match: { panel: panel.id, panelName: panel.name, section: s.title, anchor: typeof s.anchor === 'string' ? s.anchor : '', text } });
  }
  // A panel and its section of the same name (Tracing, Coding agents) are one place: keep the section, which has the anchor.
  const sectionNamed = new Set(found.filter(f => !f.whole).map(f => `${f.match.panel}\n${f.match.section}`));
  return found.filter(f => !f.whole || !sectionNamed.has(`${f.match.panel}\n${f.match.section}`))
    .sort((a, b) => a.rank - b.rank).slice(0, MAX_RESULTS).map(f => f.match);
}
