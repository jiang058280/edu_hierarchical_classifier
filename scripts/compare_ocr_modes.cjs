const fs = require('node:fs');
const path = require('node:path');
const [moduleDir, dataDir, image, output, corePath] = process.argv.slice(2);
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const { createWorker } = require(path.resolve(moduleDir));
  for (const langs of ['chi_sim', 'chi_sim+eng']) {
    const worker = await createWorker(langs.split('+'), 1, { langPath: path.resolve(dataDir), gzip: false, cacheMethod: 'none', ...(corePath ? {corePath: path.resolve(corePath)} : {}) });
    try {
      for (const mode of ['3', '6', '11']) {
        await worker.setParameters({ tessedit_pageseg_mode: mode });
        const result = await worker.recognize(fs.readFileSync(image));
        fs.writeFileSync(path.join(output, `${langs}-${mode}.txt`), result.data.text);
        console.log(langs, mode, result.data.confidence, result.data.text.length);
      }
    } finally { await worker.terminate(); }
  }
}
main().catch(() => { console.error('OCR comparison failed'); process.exit(1); });
