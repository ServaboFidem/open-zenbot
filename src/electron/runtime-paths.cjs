// Where the installed app keeps its runtime: Python, the venv, uv's cache and tools, Node.js, downloads and the
// setup stamp, all under <userData>/runtime. The bundled uv and the harness source come with the app in
// <resources>. The path flavour follows the platform argument, not the host, so Windows paths are testable
// anywhere. Node.js is unpacked with its top folder moved to <root>/node, so nodeBin is <root>/node/bin on
// Linux and macOS and <root>/node itself on Windows (where node.exe sits at the top).
const path = require('node:path');

function runtimePaths({ userData, resources, platform }) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  const exe = platform === 'win32' ? '.exe' : '';
  const root = p.join(userData, 'runtime');
  const venv = p.join(root, 'venv');
  const node = p.join(root, 'node');
  return {
    root,
    venv,
    python: platform === 'win32' ? p.join(venv, 'Scripts', 'python.exe') : p.join(venv, 'bin', 'python'),
    pythonInstalls: p.join(root, 'python'),
    uvCache: p.join(root, 'uv-cache'),
    uvTools: p.join(root, 'uv-tools'),
    node,
    nodeBin: platform === 'win32' ? node : p.join(node, 'bin'),
    downloads: p.join(root, 'downloads'),
    stamp: p.join(root, 'setup.json'),
    logs: p.join(userData, 'logs'),
    envFile: p.join(userData, '.env'),
    uv: p.join(resources, 'bin', 'uv' + exe),
    uvx: p.join(resources, 'bin', 'uvx' + exe),
    harness: p.join(resources, 'harness'),
  };
}

// The variables every uv run gets (setup here, the harness and its uvx servers later), so they all share one
// managed Python, one venv and one cache, and no uv.toml or user config on the machine changes them.
function uvEnv(paths, baseEnv) {
  return {
    ...baseEnv,
    UV_PYTHON_INSTALL_DIR: paths.pythonInstalls,
    UV_PROJECT_ENVIRONMENT: paths.venv,
    UV_CACHE_DIR: paths.uvCache,
    UV_TOOL_DIR: paths.uvTools,
    UV_PYTHON_PREFERENCE: 'only-managed',
    UV_NO_CONFIG: '1',
  };
}

module.exports = { runtimePaths, uvEnv };
