// Tesseract.js browser API: https://github.com/naptha/tesseract.js/blob/v6.0.1/docs/api.md
// Only library/model downloads leave the browser; image pixels stay in its OCR worker.
const LIBRARY_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/tesseract.esm.min.js";
const WORKER_URL = "https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/worker.min.js";
const CORE_URL = "https://cdn.jsdelivr.net/npm/tesseract.js-core@6.0.0";
const LANGUAGE_URL = "https://cdn.jsdelivr.net/npm/@tesseract.js-data/eng@1.0.0/4.0.0_best_int";
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_PIXELS = 24000000;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/bmp", "image/x-ms-bmp"]);
let languageDataPromise;

class ImageToolsLoadError extends Error {}

function englishData() {
  if (!languageDataPromise) {
    languageDataPromise = (async () => {
      const response = await fetch(LANGUAGE_URL + "/eng.traineddata.gz", { cache: "force-cache" });
      if (!response.ok) throw new ImageToolsLoadError("The English reading model could not be downloaded.");
      // Finish the public-model download so the worker can reuse HTTP cache.
      await response.arrayBuffer();
    })().catch(error => {
      languageDataPromise = undefined;
      throw error;
    });
  }
  return languageDataPromise;
}

function failure(message) {
  return { ok: false, message, blocks: [] };
}

function expectedLibraryFailure(error) {
  if (error instanceof ImageToolsLoadError) return true;
  const message = String(error?.message ?? error);
  return /failed to fetch|networkerror|network error|failed to load|error loading|importscripts|failed to execute ['"]?importscripts|loading chunk|load failed|fetching a dynamically imported module|image.*(?:decode|read)|pixread|unsupported image/i.test(message);
}

async function imageBlob(image) {
  if (image instanceof Blob) return image;
  if (typeof image !== "string" || !image) return null;
  const response = await fetch(image, { credentials: "same-origin" });
  if (!response.ok) return null;
  return response.blob();
}

async function decodeImage(blob) {
  const image = new Image();
  const url = URL.createObjectURL(blob);
  try {
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function preparedCanvas(image) {
  const width = image.naturalWidth ?? image.width;
  const height = image.naturalHeight ?? image.height;
  if (!width || !height || width < 20 || height < 20 || width * height > MAX_PIXELS || Math.max(width, height) > 12000) {
    return null;
  }
  // Bound recognition memory while preserving ordinary screenshot text.
  let scale = Math.min(1, 1600 / width);
  if (width < 800) scale = Math.min(2, 1000 / width);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  context.fillStyle = "white";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function ocrWords(page) {
  const words = [];
  for (const block of page.blocks ?? []) {
    for (const paragraph of block.paragraphs ?? []) {
      for (const line of paragraph.lines ?? []) {
        for (const word of line.words ?? []) {
          const text = String(word.text ?? "").trim();
          if (!text) continue;
          const { x0, y0, x1, y1 } = word.bbox;
          words.push({ text, left: x0, top: y0, right: x1, bottom: y1,
            confidence: Math.max(0, Math.min(1, word.confidence / 100)) });
        }
      }
    }
  }
  return words;
}

function cellBlocks(words, offset) {
  const lines = [];
  for (const word of words.sort((a, b) => (a.top + a.bottom) - (b.top + b.bottom) || a.left - b.left)) {
    const middle = (word.top + word.bottom) / 2;
    const height = word.bottom - word.top;
    let line = lines.find(candidate => Math.abs(candidate.middle - middle) <= Math.max(5, Math.min(candidate.height, height) * 0.55));
    if (!line) {
      line = { middle, height, words: [] };
      lines.push(line);
    }
    line.words.push(word);
    line.middle = line.words.reduce((sum, value) => sum + (value.top + value.bottom) / 2, 0) / line.words.length;
    line.height = Math.max(line.height, height);
  }
  const blocks = [];
  for (const line of lines) {
    let cell = null;
    for (const word of line.words.sort((a, b) => a.left - b.left)) {
      const characterWidth = (word.right - word.left) / Math.max(1, word.text.length);
      const joinGap = Math.max(7, Math.min(line.height * 0.85, characterWidth * 2));
      if (cell && word.left - cell.right <= joinGap) {
        cell.text += " " + word.text;
        cell.right = Math.max(cell.right, word.right);
        cell.top = Math.min(cell.top, word.top);
        cell.bottom = Math.max(cell.bottom, word.bottom);
        cell.confidence = Math.min(cell.confidence, word.confidence);
      } else {
        if (cell) blocks.push(cell);
        cell = { ...word };
      }
    }
    if (cell) blocks.push(cell);
  }
  return blocks.map(block => [
    [[block.left, block.top + offset], [block.right, block.top + offset],
      [block.right, block.bottom + offset], [block.left, block.bottom + offset]],
    block.text, block.confidence,
  ]);
}

export async function readHoldingsImage(imageUrl) {
  let image;
  if (imageUrl instanceof HTMLCanvasElement || (typeof OffscreenCanvas !== "undefined" && imageUrl instanceof OffscreenCanvas)) {
    image = imageUrl;
  } else {
    let blob;
    try {
      blob = await imageBlob(imageUrl);
    } catch (error) {
      if (error instanceof TypeError && /fetch|network|load/i.test(String(error.message))) {
        return failure("The image could not be loaded. Choose it again and retry.");
      }
      throw error;
    }
    if (!blob || !blob.size) return failure("Choose a readable screenshot or photo first.");
    if (blob.size > MAX_BYTES) return failure("Choose an image smaller than 10 MB.");
    if (blob.type && !IMAGE_TYPES.has(blob.type.toLowerCase())) {
      return failure("Use a PNG, JPEG, WebP or BMP image. Convert HEIC photos before uploading.");
    }
    try {
      image = await decodeImage(blob);
    } catch (error) {
      if (error.name === "EncodingError") return failure("This file could not be read as an image. Try a clear PNG or JPEG screenshot.");
      throw error;
    }
  }
  const canvas = preparedCanvas(image);
  if (!canvas) return failure("Use a readable image up to 24 megapixels and 12,000 pixels per side.");
  let worker;
  try {
    const { default: Tesseract } = await import(LIBRARY_URL);
    // Prefetch catches model-network failures before creating a worker.
    await englishData();
    worker = await Tesseract.createWorker("eng", 1, {
      workerPath: WORKER_URL, corePath: CORE_URL, langPath: LANGUAGE_URL,
      // Model cache contains only the public trained data, never the image.
      cacheMethod: "none", logger: () => {}, errorHandler: () => {},
    });
    await worker.setParameters({ tessedit_pageseg_mode: Tesseract.PSM.SPARSE_TEXT,
      preserve_interword_spaces: "1", user_defined_dpi: "150" });
    const blocks = [];
    // Tall mobile screenshots are recognized in bounded, overlapping tiles.
    for (let top = 0; top < canvas.height; top += 1700) {
      const tile = document.createElement("canvas");
      tile.width = canvas.width;
      tile.height = Math.min(1840, canvas.height - top);
      tile.getContext("2d").drawImage(canvas, 0, top, tile.width, tile.height, 0, 0, tile.width, tile.height);
      const { data } = await worker.recognize(tile, { rotateAuto: true }, { blocks: true, text: false });
      for (const block of cellBlocks(ocrWords(data), top)) {
        const duplicate = blocks.some(previous => previous[1] === block[1]
          && Math.abs(previous[0][0][0] - block[0][0][0]) < 15
          && Math.abs(previous[0][0][1] - block[0][0][1]) < 15);
        if (!duplicate) blocks.push(block);
      }
      tile.width = 0;
      tile.height = 0;
    }
    return { ok: true, blocks };
  } catch (error) {
    if (expectedLibraryFailure(error)) {
      return failure("Image reading could not load its language tools. Check your connection and try again.");
    }
    throw error;
  } finally {
    if (worker) await worker.terminate();
    canvas.width = 0;
    canvas.height = 0;
  }
}
