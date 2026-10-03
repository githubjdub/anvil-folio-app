// PDF.js API: https://mozilla.github.io/pdf.js/examples/
// Matching, pinned display/worker builds; files are read in browser memory only.
import { readHoldingsImage } from "./holdings-ocr.js";

const PDF_BASE = "https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/";
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_PAGES = 20;
const MAX_BLOCKS = 5000;
// Fixed page slots keep even twenty pages inside the parser's 30,000px bound.
const PAGE_HEIGHT = 1350;
const PAGE_SLOT = 1400;
const PAGE_WIDTH = 1600;
let pdfLibraryPromise;

function failure(message, extra = {}) {
  return { ok: false, message, blocks: [], warnings: [], page_blocks: [],
    pages_read: [], total_pages: 0, document_kind: "pdf", ...extra };
}

function expectedNetworkError(error) {
  return /failed to fetch|networkerror|network error|load failed|failed to load|fetching a dynamically imported module|importscripts|setting up fake worker failed/i.test(String(error?.message ?? error));
}

function pdfFailure(error) {
  if (error?.name === "PasswordException") {
    return failure("This PDF needs its correct password. Enter the PDF password and read it again, or choose an unlocked copy saved from your broker's statement.");
  }
  if (["InvalidPDFException", "MissingPDFException", "FormatError"].includes(error?.name)) {
    return failure("This PDF could not be read. Download a fresh statement from your broker, or upload screenshots of its holdings pages.");
  }
  if (expectedNetworkError(error)) {
    return failure("PDF reading tools could not load. Check your connection and try again.");
  }
  return null;
}

async function pdfLibrary() {
  if (!pdfLibraryPromise) {
    pdfLibraryPromise = import(PDF_BASE + "build/pdf.mjs").then(library => {
      library.GlobalWorkerOptions.workerSrc = PDF_BASE + "build/pdf.worker.mjs";
      return library;
    }).catch(error => {
      pdfLibraryPromise = undefined;
      throw error;
    });
  }
  return pdfLibraryPromise;
}

async function fileBlob(fileUrl) {
  if (fileUrl instanceof Blob) return fileUrl;
  if (typeof fileUrl !== "string" || !fileUrl) return null;
  const response = await fetch(fileUrl, { credentials: "same-origin" });
  if (!response.ok) return null;
  return response.blob();
}

function pageRange(first, last, total) {
  if (!Number.isInteger(first) || first < 1 || first > total) {
    return { message: `Choose a first page from 1 to ${total}.` };
  }
  const end = last == null ? Math.min(first + 4, total) : last;
  if (!Number.isInteger(end) || end < first || end > total) {
    return { message: `Choose a last page from ${first} to ${total}.` };
  }
  if (end - first + 1 > MAX_PAGES) {
    return { message: "Read up to 20 PDF pages at a time. Choose the range containing your holdings or transactions." };
  }
  return { first, last: end };
}

function cellBlocks(fragments) {
  const lines = [];
  for (const fragment of fragments.sort((a, b) => (a.top + a.bottom) - (b.top + b.bottom) || a.left - b.left)) {
    const middle = (fragment.top + fragment.bottom) / 2;
    const height = fragment.bottom - fragment.top;
    let line = lines.find(candidate => Math.abs(candidate.middle - middle) <= Math.max(3, Math.min(candidate.height, height) * 0.5));
    if (!line) {
      line = { middle, height, fragments: [] };
      lines.push(line);
    }
    line.fragments.push(fragment);
    line.middle = line.fragments.reduce((sum, value) => sum + (value.top + value.bottom) / 2, 0) / line.fragments.length;
    line.height = Math.max(line.height, height);
  }
  const cells = [];
  for (const line of lines) {
    let cell;
    for (const fragment of line.fragments.sort((a, b) => a.left - b.left)) {
      const characterWidth = (fragment.right - fragment.left) / Math.max(1, fragment.text.length);
      const joinGap = Math.max(4, Math.min(line.height * 0.8, characterWidth * 2));
      if (cell && fragment.left - cell.right <= joinGap) {
        cell.text += " " + fragment.text;
        cell.right = Math.max(cell.right, fragment.right);
        cell.top = Math.min(cell.top, fragment.top);
        cell.bottom = Math.max(cell.bottom, fragment.bottom);
      } else {
        if (cell) cells.push(cell);
        cell = { ...fragment };
      }
    }
    if (cell) cells.push(cell);
  }
  const blocks = [];
  for (const cell of cells) {
    // Statement boilerplate may arrive as one long PDF string. Keep every
    // extracted block inside the same parser text bound without dropping it.
    for (let start = 0; start < cell.text.length;) {
      let end = Math.min(start + 300, cell.text.length);
      if (end < cell.text.length) {
        const gap = cell.text.lastIndexOf(" ", end);
        if (gap > start) end = gap;
      }
      const text = cell.text.slice(start, end).trim();
      const left = cell.left + (cell.right - cell.left) * start / cell.text.length;
      const right = cell.left + (cell.right - cell.left) * end / cell.text.length;
      if (text) blocks.push([[[left, cell.top], [right, cell.top], [right, cell.bottom], [left, cell.bottom]], text, 1]);
      start = end;
      while (cell.text[start] === " ") start++;
    }
  }
  return blocks;
}

async function statementImageRegions(page, viewport, library) {
  const operations = await page.getOperatorList();
  const stack = [];
  const regions = [];
  let transform = [1, 0, 0, 1, 0, 0];
  const ops = library.OPS;
  for (let index = 0; index < operations.fnArray.length; index++) {
    const operation = operations.fnArray[index], args = operations.argsArray[index];
    if (operation === ops.save || operation === ops.paintFormXObjectBegin) {
      stack.push(transform.slice());
      if (operation === ops.paintFormXObjectBegin && args[0]) transform = library.Util.transform(transform, args[0]);
    } else if (operation === ops.restore || operation === ops.paintFormXObjectEnd) {
      transform = stack.pop() ?? [1, 0, 0, 1, 0, 0];
    } else if (operation === ops.transform) {
      transform = library.Util.transform(transform, args);
    } else if (operation === ops.paintImageXObject || operation === ops.paintInlineImageXObject) {
      // PDF image draws cover a transformed unit square. Keep small broker
      // logos out of OCR, and preserve native text outside larger scan regions.
      const matrix = library.Util.transform(viewport.transform, transform);
      const area = Math.abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]);
      if (area > viewport.width * viewport.height * 0.2) {
        const corners = [[0, 0], [1, 0], [1, 1], [0, 1]].map(([x, y]) => [
          matrix[0] * x + matrix[2] * y + matrix[4],
          matrix[1] * x + matrix[3] * y + matrix[5],
        ]);
        regions.push({ left: Math.max(0, Math.min(...corners.map(point => point[0]))),
          right: Math.min(viewport.width, Math.max(...corners.map(point => point[0]))),
          top: Math.max(0, Math.min(...corners.map(point => point[1]))),
          bottom: Math.min(viewport.height, Math.max(...corners.map(point => point[1]))) });
      }
    }
  }
  return regions;
}

function insideImageRegion(block, regions) {
  const x = block[0].reduce((sum, point) => sum + point[0], 0) / block[0].length;
  const y = block[0].reduce((sum, point) => sum + point[1], 0) / block[0].length;
  return regions.some(region => x >= region.left && x <= region.right && y >= region.top && y <= region.bottom);
}

function nativeBlocks(content, viewport, library) {
  const fragments = [];
  for (const item of content.items) {
    if (typeof item.str !== "string" || !item.str.trim()) continue;
    const transform = library.Util.transform(viewport.transform, item.transform);
    const style = content.styles[item.fontName] ?? {};
    let angle = Math.atan2(transform[1], transform[0]);
    if (style.vertical) angle += Math.PI / 2;
    const height = Math.hypot(transform[2], transform[3]);
    const ascent = height * (style.ascent ?? (style.descent == null ? 0.8 : 1 + style.descent));
    const width = (style.vertical ? item.height : item.width) * viewport.scale;
    const left = transform[4] + ascent * Math.sin(angle);
    const top = transform[5] - ascent * Math.cos(angle);
    const dx = width * Math.cos(angle), dy = width * Math.sin(angle);
    const hx = -height * Math.sin(angle), hy = height * Math.cos(angle);
    const xs = [left, left + dx, left + dx + hx, left + hx];
    const ys = [top, top + dy, top + dy + hy, top + hy];
    if (![...xs, ...ys].every(Number.isFinite) || !width || !height) continue;
    const fragment = { text: item.str.trim(), left: Math.max(0, Math.min(...xs)),
      right: Math.min(PAGE_WIDTH, Math.max(...xs)), top: Math.max(0, Math.min(...ys)),
      bottom: Math.min(PAGE_HEIGHT, Math.max(...ys)) };
    if (fragment.right <= fragment.left || fragment.bottom <= fragment.top) continue;
    // Some exporters emit a row as one string separated by wide whitespace.
    // Split only explicit multi-space/tab gaps; ordinary phrase headers stay intact.
    const parts = [...item.str.matchAll(/\S(?:.*?\S)?(?=\s{2,}|\t|$)/g)];
    if (parts.length > 1 && Math.abs(angle) < 0.05) {
      for (const part of parts) {
        fragments.push({ ...fragment, text: part[0],
          left: fragment.left + (fragment.right - fragment.left) * part.index / item.str.length,
          right: fragment.left + (fragment.right - fragment.left) * (part.index + part[0].length) / item.str.length });
      }
    } else {
      fragments.push(fragment);
    }
  }
  return cellBlocks(fragments);
}

function normalisedOcrBlocks(blocks, width, height) {
  const scale = Math.min(PAGE_WIDTH / width, PAGE_HEIGHT / height, 1);
  return blocks.map(([points, text, confidence]) => [
    points.map(([x, y]) => [Math.max(0, Math.min(PAGE_WIDTH, x * scale)), Math.max(0, Math.min(PAGE_HEIGHT, y * scale))]),
    text, confidence,
  ]);
}

async function scannedBlocks(page, viewport) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(viewport.width);
  canvas.height = Math.ceil(viewport.height);
  try {
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, background: "white" }).promise;
    const result = await readHoldingsImage(canvas);
    if (!result.ok) return result;
    // The image reader doubles narrow images to retain small print; normalise
    // against those recognition dimensions rather than clipping their lower rows.
    const recognitionScale = canvas.width < 800 ? Math.min(2, 1000 / canvas.width) : 1;
    return { ok: true, blocks: normalisedOcrBlocks(result.blocks,
      Math.round(canvas.width * recognitionScale), Math.round(canvas.height * recognitionScale)) };
  } finally {
    canvas.width = 0;
    canvas.height = 0;
  }
}

/** Read native PDF cells, scanned PDF pages, or the existing supported images. */
export async function readHoldingsFile(fileUrl, firstPage = 1, lastPage = null, password = "") {
  let blob;
  try {
    blob = await fileBlob(fileUrl);
  } catch (error) {
    if (expectedNetworkError(error)) return failure("The file could not be loaded. Choose it again and retry.");
    throw error;
  }
  if (!blob || !blob.size) return failure("Choose a readable PDF, screenshot or photo first.");
  if (blob.size > MAX_BYTES) return failure("Choose a file smaller than 10 MB.");
  const signature = new TextDecoder("latin1").decode(await blob.slice(0, 1024).arrayBuffer());
  const isPdf = signature.includes("%PDF-") || blob.type.toLowerCase() === "application/pdf";
  if (!isPdf) {
    const result = await readHoldingsImage(blob);
    return { ...result, warnings: [], document_kind: "image", pages_read: [1], total_pages: 1,
      page_blocks: result.ok ? [{ page: 1, blocks: result.blocks }] : [] };
  }
  let loadingTask;
  let pdf;
  try {
    const library = await pdfLibrary();
    loadingTask = library.getDocument({ data: new Uint8Array(await blob.arrayBuffer()), password,
      isEvalSupported: false, stopAtErrors: true, maxImageSize: 24000000,
      cMapUrl: PDF_BASE + "cmaps/", cMapPacked: true,
      standardFontDataUrl: PDF_BASE + "standard_fonts/", wasmUrl: PDF_BASE + "wasm/",
      verbosity: 0 });
    pdf = await loadingTask.promise;
    const range = pageRange(firstPage, lastPage, pdf.numPages);
    if (range.message) return failure(range.message, { total_pages: pdf.numPages });
    const blocks = [], pageBlocks = [], pagesRead = [], scannedPages = [];
    for (let number = range.first; number <= range.last; number++) {
      const page = await pdf.getPage(number);
      try {
        const original = page.getViewport({ scale: 1 });
        if (!original.width || !original.height || !Number.isFinite(original.width + original.height)) {
          return failure(`PDF page ${number} has an unsupported size. Upload a screenshot of that page instead.`, { total_pages: pdf.numPages });
        }
        const scale = Math.min(PAGE_WIDTH / original.width, PAGE_HEIGHT / original.height);
        const viewport = page.getViewport({ scale });
        const content = await page.getTextContent();
        if (content.items.length > 25000) {
          return failure(`PDF page ${number} contains too much text to import safely. Choose a smaller page range or a screenshot of the holdings table.`, { total_pages: pdf.numPages });
        }
        let cells = nativeBlocks(content, viewport, library);
        const textLength = cells.reduce((sum, cell) => sum + cell[1].length, 0);
        const imageRegions = await statementImageRegions(page, viewport, library);
        const needsOcr = textLength < 35 || imageRegions.length > 0;
        if (needsOcr) {
          const result = await scannedBlocks(page, viewport);
          if (!result.ok) return failure(`Page ${number}: ${result.message}`, { total_pages: pdf.numPages });
          // Long selectable broker boilerplate must not hide scanned holdings.
          // Keep exact native cells outside the images, without duplicate drafts
          // from OCR reading the same selectable headings and footers.
          cells = textLength < 35 ? result.blocks : [
            ...cells.filter(block => !insideImageRegion(block, imageRegions)),
            ...result.blocks.filter(block => insideImageRegion(block, imageRegions)),
          ];
          scannedPages.push(number);
        }
        if (blocks.length + cells.length > MAX_BLOCKS) {
          return failure("This range contains too much text to import safely. Choose fewer pages containing just your holdings or transactions.", { total_pages: pdf.numPages });
        }
        const offset = pagesRead.length * PAGE_SLOT;
        blocks.push(...cells.map(([points, text, confidence]) => [points.map(([x, y]) => [x, y + offset]), text, confidence]));
        pageBlocks.push({ page: number, blocks: cells });
        pagesRead.push(number);
      } finally {
        page.cleanup();
      }
    }
    const warnings = [];
    if (range.first !== 1 || range.last !== pdf.numPages) {
      warnings.push(`Only PDF pages ${range.first}–${range.last} of ${pdf.numPages} were read. Select another page range to read any remaining holdings or transactions.`);
    }
    if (scannedPages.length) {
      warnings.push(`Scanned PDF page${scannedPages.length === 1 ? "" : "s"} ${scannedPages.join(", ")} ${scannedPages.length === 1 ? "was" : "were"} read from images. Check all detected fields against the statement.`);
    }
    return { ok: true, blocks, warnings, document_kind: "pdf", pages_read: pagesRead,
      total_pages: pdf.numPages, page_blocks: pageBlocks };
  } catch (error) {
    const result = pdfFailure(error);
    if (result) return result;
    throw error;
  } finally {
    // Destroying the task releases its worker, fonts and document bytes.
    if (loadingTask) await loadingTask.destroy();
  }
}
