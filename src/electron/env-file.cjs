// The repo's .env, read when the app starts, like the harness does: a setting the shell already has
// wins over the file. Node's own parser (util.parseEnv): comments, quotes, no dependency.
const fs = require('node:fs');
const { parseEnv } = require('node:util');

/** Copy the file's settings into `env` where unset. Returns the names it set, never the values. */
function loadEnvFile(file, env = process.env) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return [];  // no .env: nothing to load
  }
  const loaded = [];
  for (const [name, value] of Object.entries(parseEnv(text))) {
    if (Object.prototype.hasOwnProperty.call(env, name)) continue;
    env[name] = value;
    loaded.push(name);
  }
  return loaded;
}

module.exports = { loadEnvFile };
