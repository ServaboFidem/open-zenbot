// Where ZenBot's Telnyx texts go and when: the owner's phone, the reminder lead time, the evening rundown time and
// the two switches. Only this module writes telnyx_texts.json; the harness only reads it (src/telnyx/settings.py
// read_texts, with the same rules). The harness API has no sign-in, so a harness route that saved the phone would
// let a bot point ZenBot's texts at any number.
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const LEADS = [10, 15, 30, 60, 120];
const DEFAULTS = Object.freeze({ phone: '', lead_minutes: 30, evening: '20:00', before: false, rundown: false });

function textsSettings(raw) {
  const typed = raw?.phone;
  if (typeof typed !== 'string') throw new Error('Enter your phone number with its country code, such as +15551234567.');
  const phone = typed.replace(/[\s().-]/g, '');
  if (phone && !/^\+[1-9]\d{7,14}$/.test(phone)) {
    throw new Error('Enter your phone number with its country code, such as +15551234567.');
  }
  if (!LEADS.includes(raw.lead_minutes)) throw new Error('Choose a reminder time from the list.');
  if (typeof raw.evening !== 'string' || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(raw.evening)) {
    throw new Error('Choose an evening time.');
  }
  if (typeof raw.before !== 'boolean' || typeof raw.rundown !== 'boolean') throw new Error('Turn each text on or off.');
  if (!phone && (raw.before || raw.rundown)) throw new Error('Enter your phone number to turn on texts.');
  return { phone, lead_minutes: raw.lead_minutes, evening: raw.evening, before: raw.before, rundown: raw.rundown };
}

function registerTelnyxTexts(ipc, windows, settings) {
  const file = path.join(settings, 'telnyx_texts.json');
  const handle = (channel, fn) => ipc.handle(channel, (event, input) => {
    if (!windows.has(event.sender) || event.senderFrame !== event.sender.mainFrame ||
        event.senderFrame?.url !== 'app://openzenbot/index.html') throw new Error('Not allowed.');
    return fn(input);
  });
  handle('telnyx-texts:get', () => {
    try { return textsSettings(JSON.parse(fs.readFileSync(file, 'utf8'))); }
    catch (error) {
      if (error.code === 'ENOENT') return { ...DEFAULTS };
      throw new Error('The saved text settings could not be read.');
    }
  });
  handle('telnyx-texts:save', input => {
    const value = textsSettings(input);
    fs.mkdirSync(settings, { recursive: true, mode: 0o700 });
    const temporary = path.join(settings, `telnyx_texts.${randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600 });
      fs.renameSync(temporary, file);
    } finally { fs.rmSync(temporary, { force: true }); }
    return value;
  });
}

module.exports = { registerTelnyxTexts, textsSettings };
