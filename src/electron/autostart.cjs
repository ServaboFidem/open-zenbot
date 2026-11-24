// Start at sign-in on Linux: Electron's setLoginItemSettings does nothing there, so the app keeps an XDG autostart
// entry of its own, ~/.config/autostart/openzenbot.desktop (under $XDG_CONFIG_HOME when that is set). Per user: no
// root, nothing outside the user's own config folder. The file operations arrive as arguments, so tests never touch
// a real home folder.
const NAME = 'openzenbot.desktop';

// the Exec value: the program's path quoted (spaces, and " ` $ \ escaped as the Desktop Entry spec says; a
// literal backslash is doubled again because the file's string syntax reads \ as one), then --hidden
function execLine(execPath) {
  const BS = String.fromCharCode(92);
  const quoted = [...String(execPath)].map(c => {
    if (c === BS) return BS + BS + BS + BS;  //  escaped for Exec, then doubled for the file's string syntax
    if (c === '"' || c === '`' || c === '$') return BS + BS + c;
    return c === '%' ? '%%' : c;
  }).join('');
  return `"${quoted}" --hidden`;
}

function entry(execPath) {
  return `[Desktop Entry]\nType=Application\nName=OpenZenBot\nExec=${execLine(execPath)}\nX-GNOME-Autostart-enabled=true\n`;
}

// fs: { mkdir(dir), write(file, text), rename(from, to), remove(file), exists(file) }, all synchronous
function createAutostart({ fs, env = {}, home, execPath, join = (...p) => p.join('/') }) {
  const base = env.XDG_CONFIG_HOME && env.XDG_CONFIG_HOME.trim() ? env.XDG_CONFIG_HOME : join(home, '.config');
  const dir = join(base, 'autostart');
  const file = join(dir, NAME);
  return {
    file,
    enabled: () => { try { return !!fs.exists(file); } catch { return false; } },
    enable() {
      fs.mkdir(dir);
      const tmp = `${file}.tmp`;
      fs.write(tmp, entry(execPath));
      fs.rename(tmp, file);
    },
    disable() { fs.remove(file); },
  };
}

module.exports = { createAutostart, execLine, entry, NAME };
