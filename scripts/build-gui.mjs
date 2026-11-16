// Bundle the renderer into dist/ and copy its page beside it. Electron and
// a static dev server both serve dist/ as the app's root.
import { build } from 'esbuild';
import { copyFileSync, mkdirSync } from 'node:fs';

mkdirSync('dist', { recursive: true });
await build({
  entryPoints: ['src/gui/app.jsx'],
  bundle: true,
  minify: true,
  outfile: 'dist/app.js',
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.svg': 'file', '.png': 'file' },
  assetNames: 'assets/[name]',
  logLevel: 'info',
});
// The installed app's setup page: a plain script of its own, no React (src/gui/setup.html)
await build({
  entryPoints: ['src/gui/setup.js'],
  bundle: true,
  minify: true,
  format: 'iife',
  outfile: 'dist/setup.js',
  logLevel: 'info',
});
// The preload runs sandboxed, where require() reaches only electron: bundle what it loads.
await build({
  entryPoints: ['src/electron/preload.cjs'],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  external: ['electron'],
  outfile: 'dist/preload.cjs',
  logLevel: 'info',
});
copyFileSync('src/gui/index.html', 'dist/index.html');
copyFileSync('src/gui/setup.html', 'dist/setup.html');
mkdirSync('dist/assets', { recursive: true });
copyFileSync('src/assets/zenbot-enso.svg', 'dist/assets/zenbot-enso.svg');
// pdf.js parses PDFs in a worker the page loads from its own origin (CSP worker-src 'self')
copyFileSync('node_modules/pdfjs-dist/build/pdf.worker.min.mjs', 'dist/pdf.worker.mjs');
