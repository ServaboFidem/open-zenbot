// The real side effects behind setup.cjs and harness-process.cjs for the installed app: starting a child and
// stopping its whole tree, unpacking an archive with the system tar, free disk space, the first node on PATH,
// whether something listens on a port, and the stamp and lock hash that needsSetup compares. Every child is
// started from an argument array with no shell. The Node modules are passed in where a test needs a fake.
const crypto = require('node:crypto');
const path = require('node:path');

const ABORT_KILL_MS = 3000;
const NODE_MIN_MAJOR = 24;

const systemExe = (env, name) => path.win32.join(env.SystemRoot || env.SYSTEMROOT || 'C:\\Windows', 'System32', name);

// Calls onLine for each line of a stream (\n, \r\n or a bare \r ends a line), the last one too.
function eachLine(stream, onLine) {
  if (!stream || typeof stream.on !== 'function') return;
  let partial = '';
  stream.on('data', chunk => {
    const parts = (partial + String(chunk)).split(/\r\n|\n|\r/);
    partial = parts.pop();
    parts.forEach(l => { if (l) onLine(l); });
  });
  stream.on('end', () => { if (partial) onLine(partial); partial = ''; });
}

// Windows: taskkill /PID <pid> /T /F (the whole tree, forced, as scripts/dev.ps1 does). Resolves either way.
function killTreeWith({ execFile, env = process.env }) {
  return pid => new Promise(resolve => {
    if (!pid) { resolve(); return; }
    execFile(systemExe(env, 'taskkill.exe'), ['/PID', String(pid), '/T', '/F'], { windowsHide: true }, () => resolve());
  });
}

// run(cmd, args, { env, cwd, onLine, signal }) -> Promise<{ code }> for setup.cjs. A child that cannot start
// resolves code -1 with a line saying so. On signal's abort the whole tree is stopped: on Linux and macOS the
// child leads its own process group, which gets TERM, then KILL after 3 s; on Windows killTree.
function createRun({ spawn, platform, kill = process.kill.bind(process), killTree, log = () => {} }) {
  return (cmd, args, { env, cwd, onLine = () => {}, signal } = {}) => new Promise(resolve => {
    const line = l => { try { log(l); } catch { /* a broken log must not stop setup */ } onLine(l); };
    const cannot = e => line(`Could not start ${cmd}: ${e && e.code === 'ENOENT' ? 'the program is not there' : (e && e.message) || e}`);
    let child;
    try {
      child = spawn(cmd, args, { env, cwd, detached: platform !== 'win32', windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch (e) {
      cannot(e);
      resolve({ code: -1 });
      return;
    }
    let done = false;
    let timer = null;
    function onAbort() {
      if (!child.pid) return;
      if (platform === 'win32') { killTree(child.pid); return; }
      const send = sig => { try { kill(-child.pid, sig); } catch { /* already gone */ } };
      send('SIGTERM');
      timer = setTimeout(() => send('SIGKILL'), ABORT_KILL_MS);
    }
    const finish = code => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (signal) signal.removeEventListener('abort', onAbort);
      resolve({ code });
    };
    eachLine(child.stdout, line);
    eachLine(child.stderr, line);
    child.on('error', e => { cannot(e); finish(-1); });
    child.on('close', code => finish(code ?? -1));
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

// extract(archive, destDir): unpacks as it is, top folder included, with the system tar (Windows 10 and later
// ship bsdtar as System32\tar.exe, which reads .zip; Linux reads .tar.xz).
function createExtract({ run, platform, env = process.env }) {
  const tar = platform === 'win32' ? systemExe(env, 'tar.exe') : 'tar';
  return async (archive, destDir) => {
    const out = [];
    const { code } = await run(tar, ['-xf', archive, '-C', destDir], { env, cwd: destDir, onLine: l => out.push(l) });
    if (code !== 0) throw new Error(`Could not unpack ${path.basename(archive)}: ${out.slice(-5).join(' ') || `tar exited with ${code}`}`);
  };
}

const freeBytesWith = fs => async dir => {
  const s = await fs.promises.statfs(dir);
  return Number(s.bavail) * Number(s.bsize);
};

const nodeMajor = text => {
  const m = /^v?(\d+)\.\d+\.\d+/.exec(String(text).trim());
  return m ? Number(m[1]) : null;
};

// The node a child would get from PATH: the first one found, and only when it is Node 24 or newer. Only
// absolute PATH folders are looked in, so the current folder never counts.
function findNodeWith({ env = process.env, platform, fs, execFile }) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  const key = Object.keys(env).find(k => (platform === 'win32' ? k.toUpperCase() === 'PATH' : k === 'PATH'));
  const dirs = String((key && env[key]) || '').split(platform === 'win32' ? ';' : ':').filter(d => d && p.isAbsolute(d));
  return async () => {
    for (const dir of dirs) {
      const file = p.join(dir, platform === 'win32' ? 'node.exe' : 'node');
      let isFile = false;
      try { isFile = fs.statSync(file).isFile(); } catch { /* not here */ }
      if (!isFile) continue;
      const version = await new Promise(resolve => {
        execFile(file, ['--version'], { timeout: 10_000, windowsHide: true }, (err, stdout) => resolve(err ? null : stdout));
      });
      const major = version === null ? null : nodeMajor(version);
      return major !== null && major >= NODE_MIN_MAJOR ? file : null;
    }
    return null;
  };
}

// Something accepts connections on 127.0.0.1:port.
function portInUseWith(net) {
  return port => new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const done = v => { socket.destroy(); resolve(v); };
    socket.setTimeout(2000, () => done(false));
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
  });
}

// SHA-256 of a file, '' when it cannot be read. An empty hash is still compared like any other: it differs from a
// stamp's real hash, so setup runs, and that setup's `uv sync --frozen` fails without the lock file, so no stamp
// with an empty hash is written.
function fileSha256(fs, file) {
  try { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); } catch { return ''; }
}

// An existing file (statSync follows a link, so a venv python whose managed Python was deleted is not one).
function isFile(fs, file) {
  try { return fs.statSync(file).isFile(); } catch { return false; }
}

function readStamp(fs, file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

module.exports = { createRun, createExtract, killTreeWith, freeBytesWith, findNodeWith, nodeMajor, portInUseWith, fileSha256, readStamp, isFile };
