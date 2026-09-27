const assert = require('node:assert/strict');
const { groups, regions } = require('../edu_core/rag/loaders/ocr_layout.cjs');
assert.deepEqual(groups([1, 2, 5, 8, 9]), [[1, 2], [5, 5], [8, 9]]);
const width = 200, height = 600;
const pixels = Buffer.alloc(width * height, 255);
function rect(x, y, w, h) {
  for (let yy = y; yy < y + h; yy++) for (let xx = x; xx < x + w; xx++) pixels[yy * width + xx] = 0;
}
// No source-specific coordinates in the detector: synthetic independent layout.
rect(25, 25, 80, 8);
for (const y of [100, 130, 160]) rect(20, y, 161, 2);
for (const x of [20, 60, 100, 140, 180]) rect(x, 100, 2, 62);
rect(25, 205, 85, 8);
const result = regions(pixels, width, height);
assert.equal(result.tables.length, 1);
assert.equal(result.tables[0].rows.length, 3);
assert.equal(result.tables[0].columns.length, 5);
assert.deepEqual(result.lines.map(x => x.top), [25, 205]);
assert.deepEqual(regions(Buffer.alloc(width * height, 255), width, height), { lines: [], tables: [] });
console.log('OCR layout detector tests passed');
