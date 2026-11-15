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
// pdf.js parses PDFs in a worker the page loads from its own origin (CSP worker-src 'self')
copyFileSync('node_modules/pdfjs-dist/build/pdf.worker.min.mjs', 'dist/pdf.worker.mjs');
