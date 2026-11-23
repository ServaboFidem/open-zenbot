const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const atom = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+$/;
const label = /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/i;

function emailSettings(raw) {
  const recipient = raw?.recipient?.trim();
  if (typeof recipient !== 'string') throw new Error('Enter a recipient or clear the field.');
  if (recipient) {
    const parts = recipient.split('@');
    const local = parts[0], domain = (parts[1] || '').split('.');
    if (recipient.length > 254 || parts.length !== 2 || local.length > 64 || /\s/.test(recipient) ||
        !local.split('.').every(word => atom.test(word)) || domain.length < 2 || !domain.every(word => label.test(word))) {
      throw new Error('Enter one email address, such as you@example.com.');
    }
  }
  if (typeof raw.time !== 'string' || raw.time.length !== 5 || !/^(?:0\d|1[01]):[0-5]\d$/.test(raw.time)) {
    throw new Error('Choose a local time before noon.');
  }
  return { recipient, time: raw.time };
}

function registerMorningEmail(ipc, windows, settings) {
  const file = path.join(settings, 'morning_email.json');
  const handle = (channel, fn) => ipc.handle(channel, (event, input) => {
    if (!windows.has(event.sender) || event.senderFrame !== event.sender.mainFrame ||
        event.senderFrame?.url !== 'app://openzenbot/index.html') throw new Error('Not allowed.');
    return fn(input);
  });
  handle('morning-email:get', () => {
    try { return emailSettings(JSON.parse(fs.readFileSync(file, 'utf8'))); }
    catch (error) {
      if (error.code === 'ENOENT') return { recipient: '', time: '07:00' };
      throw new Error('The saved morning email settings could not be read.');
    }
  });
  handle('morning-email:save', input => {
    const value = emailSettings(input);
    fs.mkdirSync(settings, { recursive: true, mode: 0o700 });
    const temporary = path.join(settings, `morning_email.${randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600 });
      fs.renameSync(temporary, file);
    } finally { fs.rmSync(temporary, { force: true }); }
    return value;
  });
}

module.exports = { registerMorningEmail };
