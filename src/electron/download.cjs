// Pinned downloads: a file is renamed to its destination only after its whole content has been hashed and
// matches the pinned SHA-256. The bytes stream to dest + '.part' while they are hashed; a mismatch, a cut
// stream or a failed fetch deletes the .part, so nothing unverified is ever at dest. fetch(url) and fs (the
// node:fs API) are passed in, so this file needs no Electron and tests need no network.
const crypto = require('node:crypto');
const { pipeline } = require('node:stream/promises');
const { Readable } = require('node:stream');

class ChecksumError extends Error {
  constructor(message) { super(message); this.name = 'ChecksumError'; }
}
class OfflineError extends Error {
  constructor(message = 'No internet connection, or the download server did not answer.') { super(message); this.name = 'OfflineError'; }
}

function pinFor(pins, name, platform, arch) {
  const key = `${platform}-${arch}`;
  const pin = pins && pins[name] && pins[name][key];
  if (!pin || typeof pin !== 'object' || !pin.url || !pin.sha256) throw new Error(`No pinned ${name} download for ${key}.`);
  return { url: pin.url, sha256: pin.sha256 };
}

function hashFile(fs, file) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha256');
    fs.createReadStream(file).on('data', c => h.update(c)).on('error', reject).on('end', () => resolve(h.digest('hex')));
  });
}

async function downloadVerified({ url, dest, sha256, fetch, fs, onProgress = () => {} }) {
  const want = String(sha256).toLowerCase();
  if (fs.existsSync(dest)) {
    let have = null;
    try { have = await hashFile(fs, dest); } catch { /* unreadable: download again */ }
    if (have === want) return dest;
  }
  const part = dest + '.part';
  const drop = () => fs.promises.rm(part, { force: true }).catch(() => {});
  await drop();

  let res;
  try { res = await fetch(url); } catch { throw new OfflineError(); }
  if (!res || !res.ok || !res.body) throw new OfflineError();
  const length = Number(res.headers && res.headers.get && res.headers.get('content-length'));
  const total = Number.isFinite(length) && length > 0 ? length : null;

  const hash = crypto.createHash('sha256');
  let received = 0;
  let cut = false;
  async function* counted() {
    try {
      for await (const chunk of res.body) {
        hash.update(chunk);
        received += chunk.length;
        yield chunk;
        onProgress({ received, total });
      }
    } catch { cut = true; }
  }
  try {
    await pipeline(Readable.from(counted()), fs.createWriteStream(part));
  } catch (e) {
    await drop();
    throw e;
  }
  if (cut) { await drop(); throw new OfflineError(); }
  if (hash.digest('hex') !== want) {
    await drop();
    throw new ChecksumError(`The downloaded file does not match its pinned checksum (${url}).`);
  }
  try { await fs.promises.rename(part, dest); } catch (e) { await drop(); throw e; }
  return dest;
}

module.exports = { downloadVerified, pinFor, ChecksumError, OfflineError };
