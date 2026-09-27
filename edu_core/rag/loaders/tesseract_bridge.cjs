// Optional local backend; all language assets must already exist on disk.
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const [moduleDir, dataDir, image, output, languages, layoutMode] = process.argv.slice(2);
  if (!moduleDir || !dataDir || !image || !output || !/^[a-zA-Z0-9_]+(\+[a-zA-Z0-9_]+)*$/.test(languages || '')) {
    throw new Error('Invalid OCR arguments');
  }
  for (const localPath of [moduleDir, dataDir, image, output]) {
    if (!path.isAbsolute(localPath)) throw new Error('Local absolute paths required');
  }
  for (const lang of languages.split('+')) {
    if (!fs.statSync(path.join(dataDir, `${lang}.traineddata`)).isFile()) throw new Error('Missing local language');
  }
  const { createWorker } = require(moduleDir);
  let worker;
  try {
    worker = await createWorker(languages.split('+'), 1, {
      langPath: dataDir, gzip: false, cacheMethod: 'none',
      logger: () => {}, errorHandler: () => {},
    });
    await worker.setParameters({ tessedit_pageseg_mode: '3' });
    let text;
    if (layoutMode === 'lines_tables') {
      const sharp = require(require.resolve('sharp', { paths: [moduleDir] }));
      text = await require('./ocr_layout.cjs').recognizeLayout(worker, fs.readFileSync(image), sharp);
    } else {
      const { data } = await worker.recognize(fs.readFileSync(image));
      text = data.text;
    }
    if (!text || !text.trim()) throw new Error('Empty OCR result');
    fs.writeFileSync(output, text, { encoding: 'utf8', flag: 'wx' });
  } finally {
    if (worker) await worker.terminate();
  }
}

main().catch(() => { process.stderr.write('Local OCR failed\n'); process.exit(1); });
