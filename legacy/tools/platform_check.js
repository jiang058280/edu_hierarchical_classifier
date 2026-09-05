/**
 * 教育题目分层分类系统 - platform.html 静态自检
 *   1. 内联 JS 语法检查（vm.Script）
 *   2. 关键 DOM id 完整性检查
 *   3. iframe 指向 7860、postMessage 事件名等关键约束检查
 * 用法：node tools/platform_check.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'platform.html');
const html = fs.readFileSync(file, 'utf8');
let failed = false;

// ---- 1. 内联 JS 语法 ----
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (!blocks.length) { console.error('未找到内联 <script>'); process.exitCode = 1; process.exit(1); }
blocks.forEach((code, i) => {
  try {
    new vm.Script(code, { filename: `platform-inline-${i}.js` });
    console.log(`[OK] 内联 JS 块 #${i} 语法正确（${code.length} 字符）`);
  } catch (e) {
    failed = true;
    console.error(`[FAIL] 内联 JS 块 #${i} 语法错误: ${e.message}`);
  }
});

// ---- 2. 关键 DOM id ----
const ids = [
  'sidebar', 'btnFold', 'selClass', 'btnBell', 'btnAvatar',
  'eduFrame', 'backendOverlay', 'btnRetry',
  'sumBody', 'labelRows', 'fixMenu',
  'btnSave', 'btnBasket', 'btnReclassify',
  'histList', 'stDot', 'stCount', 'notifyPanel', 'dlg', 'dlgHist', 'toast',
];
const missing = ids.filter((id) => !html.includes(`id="${id}"`));
if (missing.length) { failed = true; console.error(`[FAIL] 缺失 DOM id: ${missing.join(', ')}`); }
else console.log(`[OK] 全部 ${ids.length} 个关键 DOM id 存在`);

// ---- 3. 关键约束 ----
const checks = [
  { name: 'iframe 动态指向 /classifier/', re: /eduFrame'\)\.src\s*=\s*/ },
  { name: 'iframe 指向 127.0.0.1:7860/classifier', re: /127\.0\.0\.1:7860\/classifier/ },
  { name: '监听 postMessage 事件', re: /addEventListener\('message'/ },
  { name: 'edu-ready 心跳处理', re: /type\s*===\s*'edu-ready'/ },
  { name: 'edu-classify 联动处理', re: /type\s*===\s*'edu-classify'/ },
  { name: 'edu-feedback 计数同步', re: /type\s*===\s*'edu-feedback'/ },
  { name: '真实模型名 BERT-base-Chinese（微调）', re: /BERT-base-Chinese（微调）/ },
  { name: '主色 #5B6FF5', re: /#5B6FF5/ },
  { name: '背景 #F4F6FA', re: /#F4F6FA/ },
  { name: '折叠按钮', re: /btnFold/ },
  { name: '班级下拉', re: /selClass/ },
  { name: '修正按钮', re: /✏️ 修正/ },
  { name: '演示角标', re: /demo-badge/ },
  { name: '100vh 无滚动', re: /height:\s*100vh/ },
  { name: '侧边栏折叠宽度 64px', re: /width:\s*64px/ },
];
checks.forEach((c) => {
  if (c.re.test(html)) console.log(`[OK] ${c.name}`);
  else { failed = true; console.error(`[FAIL] 缺少: ${c.name}`); }
});

console.log(failed ? 'PLATFORM_CHECK_FAIL' : 'PLATFORM_CHECK_DONE');
process.exit(failed ? 1 : 0);
