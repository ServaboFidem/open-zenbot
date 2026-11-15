// PDF previews with pdf.js, bundled with the app: never a frame or Chromium's PDF plugin, so a
// bot-made attachment keeps its sandbox and the window never needs plugins. The PDF's bytes come from
// the harness (an attachment or the media route) and are drawn onto a canvas. isEvalSupported: false
// keeps pdf.js from compiling code out of a PDF; it never runs a PDF's own scripts.
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist';

GlobalWorkerOptions.workerSrc = './pdf.worker.mjs';  // copied beside app.js by scripts/build-gui.mjs

/** The PDF at `url` (a harness URL), loaded. Throws when the harness does not hand it over. */
export async function loadPdf(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`The PDF could not be loaded (${res.status}).`);
  const data = new Uint8Array(await res.arrayBuffer());
  return getDocument({ data, isEvalSupported: false }).promise;
}

/** Open a document, hand it to `use` while `alive()` holds, and destroy it whatever happens: each
 *  document owns a pdf.js worker until destroyed, and a card can leave while its PDF still loads. */
export async function usingPdf(open, use, alive = () => true) {
  const doc = await open();
  try {
    return alive() ? await use(doc) : undefined;
  } finally {
    await doc.destroy();
  }
}

/** Draw page `pageNumber` (1-based) onto `canvas`, `cssWidth` pixels wide, sharp on high-density screens. */
export async function renderPage(doc, pageNumber, canvas, cssWidth) {
  const page = await doc.getPage(pageNumber);
  const unscaled = page.getViewport({ scale: 1 });
  const ratio = globalThis.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: (cssWidth / unscaled.width) * ratio });
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${Math.floor(viewport.height / ratio)}px`;
  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
}
