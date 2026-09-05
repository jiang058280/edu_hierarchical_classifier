/**
 * 教育题目分层分类系统 - 端到端桥接自测（CDP 驱动 headless Edge）
 * 验证新版 Gradio 界面：
 *   1. 无滚动一屏（scrollHeight == innerHeight）
 *   2. 快捷示例按钮填充输入框
 *   3. 「开始分类」调用真实后端（REST）→ 结果时间轴 + 圆环 + 统计更新
 *   4. 反馈按钮 → 计数更新
 * 用法：node tools/cdp_bridge_test.js  （需先启动 headless Edge + 7860 服务）
 */
const CDP_PORT = 9222;
const APP_URL = 'http://127.0.0.1:7860/';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getPageTarget() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json`);
      const targets = await res.json();
      // 优先选中本应用的页面，避免 Edge 首次运行对话框等杂项 target
      const page =
        targets.find((t) => t.type === 'page' && t.url.includes('127.0.0.1:7860')) ||
        targets.find((t) => t.type === 'page');
      if (page) return page;
    } catch (_) {}
    await sleep(300);
  }
  throw new Error('未找到 CDP page target（Edge 是否已启动？）');
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    ws.onopen = () => resolve(ws);
    ws.onerror = (e) => reject(new Error('WebSocket 连接失败: ' + e.message));
  });
}

let msgId = 0;
const pending = new Map();

function send(ws, method, params) {
  return new Promise((resolve, reject) => {
    const id = ++msgId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function main() {
  const target = await getPageTarget();
  const ws = await connect(target.webSocketDebuggerUrl);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result);
    }
  };

  async function evalJS(expression) {
    const r = await send(ws, 'Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    }
    return r.result ? r.result.value : undefined;
  }

  async function waitFor(expr, timeoutMs, desc) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await evalJS(expr)) return;
      await sleep(300);
    }
    throw new Error('等待超时: ' + desc);
  }

  // 0. 强制刷新页面（确保加载最新 JS），并等待页面与示例按钮就绪
  await send(ws, 'Page.navigate', { url: APP_URL });
  await waitFor(
    `document.getElementById('btnClassify') !== null && document.querySelectorAll('#exampleGrid button').length >= 9`,
    20000, '页面加载与示例按钮生成',
  );

  // 1. 无滚动一屏检测
  const scroll = await evalJS(`(() => ({
    innerW: window.innerWidth, innerH: window.innerHeight,
    docW: document.documentElement.scrollWidth,
    docH: document.documentElement.scrollHeight,
    vScroll: document.documentElement.scrollHeight > window.innerHeight,
    hScroll: document.documentElement.scrollWidth > window.innerWidth
  }))()`);
  console.log('[1] 布局检测:', JSON.stringify(scroll));

  // 2. 示例按钮填充
  const exampleCount = await evalJS(`document.querySelectorAll('#exampleGrid button').length`);
  await evalJS(`document.querySelectorAll('#exampleGrid button')[1].click()`);
  await sleep(200);
  const filled = await evalJS(`document.getElementById('inputText').value`);
  console.log('[2] 示例按钮数:', exampleCount, '| 填充内容:', JSON.stringify(filled.slice(0, 24)));

  // 3. 开始分类（真实后端）
  await evalJS(`document.getElementById('btnClassify').click()`);
  await waitFor(`document.querySelector('#resultBody .tl-node') !== null`, 30000, '分类结果渲染');
  const result = await evalJS(`(() => {
    var labels = Array.from(document.querySelectorAll('#resultBody .tl-label')).map(function (e) { return e.textContent; });
    var values = Array.from(document.querySelectorAll('#resultBody .tl-value')).map(function (e) { return e.textContent; });
    return {
      labels: labels,
      values: values,
      rings: document.querySelectorAll('#resultBody .ring-fg').length,
      ringNum: document.querySelector('#resultBody .ring-num') ? document.querySelector('#resultBody .ring-num').textContent : '',
      statCount: document.getElementById('statCount').textContent,
      statConf: document.getElementById('statConf').textContent
    };
  })()`);
  console.log('[3] 分类结果:', JSON.stringify(result));

  // 4. 反馈 → 计数更新（记住点击前的值）
  const beforeOk = parseInt(await evalJS(`document.getElementById('countOk').textContent`), 10);
  await evalJS(`document.getElementById('fbOk').click()`);
  await waitFor(
    `parseInt(document.getElementById('countOk').textContent, 10) > ${beforeOk}`,
    15000, '反馈计数更新',
  );
  const fb = await evalJS(`(() => ({
    ok: document.getElementById('countOk').textContent,
    bad: document.getElementById('countBad').textContent
  }))()`);
  console.log('[4] 反馈计数:', JSON.stringify(fb));

  console.log('SELFTEST_DONE');
  process.exit(0);
}

main().catch((e) => {
  console.error('SELFTEST_FAIL:', e.message);
  process.exit(1);
});
