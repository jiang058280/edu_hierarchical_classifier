// Conservative layout for upright, single-column pages with simple ruled tables.
function groups(values) {
  const result = [];
  for (const n of values) {
    const last = result[result.length - 1];
    if (last && n <= last[1] + 1) last[1] = n;
    else result.push([n, n]);
  }
  return result;
}

function regions(pixels, width, height) {
  const rowCounts = new Int32Array(height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) if (pixels[y * width + x] < 160) rowCounts[y]++;
  }
  const rules = groups(Array.from(rowCounts, (n, y) => n > width * .55 ? y : -1).filter(y => y >= 0));
  const tables = [];
  for (let i = 0; i < rules.length - 2;) {
    let j = i;
    while (j + 1 < rules.length && rules[j + 1][0] - rules[j][1] < height * .08) j++;
    if (j - i >= 2) {
      const top = rules[i][0], bottom = rules[j][1];
      const columns = [];
      for (let x = 0; x < width; x++) {
        let count = 0;
        for (let y = top; y <= bottom; y++) if (pixels[y * width + x] < 160) count++;
        if (count > (bottom - top) * .8) columns.push(x);
      }
      const borders = groups(columns);
      if (borders.length >= 3 && borders.length <= 21) {
        tables.push({ top, bottom, rows: rules.slice(i, j + 1), columns: borders });
      }
    }
    i = Math.max(j + 1, i + 1);
  }
  const ignored = y => rules.some(([a, b]) => y >= a && y <= b) || tables.some(t => y >= t.top && y <= t.bottom);
  const lines = groups(Array.from(rowCounts, (n, y) => n >= 3 && !ignored(y) ? y : -1).filter(y => y >= 0))
    .filter(([a, b]) => b - a >= 3)
    .map(([a, b]) => {
      let left = width, right = 0;
      for (let y = a; y <= b; y++) for (let x = 0; x < width; x++) {
        if (pixels[y * width + x] < 160) { left = Math.min(left, x); right = Math.max(right, x); }
      }
      return { top: a, left, width: right - left + 1, height: b - a + 1 };
    });
  return { lines, tables };
}

async function recognizeLayout(worker, image, sharp) {
  const { data, info } = await sharp(image).flatten({ background: 'white' }).greyscale().raw().toBuffer({ resolveWithObject: true });
  const layout = regions(data, info.width, info.height);
  if (layout.lines.length > 150 || layout.tables.length > 10) throw new Error('Layout limits exceeded');
  const items = [...layout.lines.map(line => ({ top: line.top, line })),
                 ...layout.tables.map(table => ({ top: table.top, table }))].sort((a, b) => a.top - b.top);
  async function recognize(rect, mode) {
    const crop = await sharp(image).extract(rect).extend({ top: 12, bottom: 12, left: 12, right: 12, background: 'white' }).png().toBuffer();
    await worker.setParameters({ tessedit_pageseg_mode: mode });
    const { data: result } = await worker.recognize(crop);
    return result.text.trim().replace(/\s*\n\s*/g, ' ').replace(/([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])/g, '$1');
  }
  const result = [];
  for (const item of items) {
    if (item.line) result.push(await recognize(item.line, '7'));
    else {
      const { rows, columns } = item.table;
      if ((rows.length - 1) * (columns.length - 1) > 100) throw new Error('Table limits exceeded');
      for (let r = 0; r < rows.length - 1; r++) {
        const cells = [];
        for (let c = 0; c < columns.length - 1; c++) {
          const left = columns[c][1] + 3, top = rows[r][1] + 3;
          const width = columns[c + 1][0] - left - 2, height = rows[r + 1][0] - top - 2;
          if (width <= 0 || height <= 0) throw new Error('Invalid table cell');
          cells.push(await recognize({ left, top, width, height }, '6'));
        }
        result.push('| ' + cells.join(' | ') + ' |');
      }
    }
  }
  return result.join('\n\n');
}
module.exports = { groups, regions, recognizeLayout };
