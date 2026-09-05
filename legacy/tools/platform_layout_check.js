/**
 * platform.html 布局回归验证：多视口下右侧模块不可被挤压为 0
 * 复验：窄视口（< 1300px）时 .main-right 需保持可见（≥ 320px）
 * 用法：node tools/platform_layout_check.js （需后端 7860 + headless Edge CDP 9222）
 */
const CDP_PORT = 9222;
const URL = 'http://127.0.0.1:7860/';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function jsonGet(u) { const r = await fetch(u); return r.json(); }

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  return new Promise((resolve, reject) => {
    let msgId = 0;
    const pending = new Map();
    const onready = [];
    let ready = false;
    ws.onopen = () => { ready = true; onready.splice(0).forEach((f) => f()); };
    ws.onerror = (e) => reject(new Error('WS 连接失败: ' + e.message));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const p = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result);
      }
    };
    const send = (method, params) => new Promise((resolve, reject) => {
      const id = ++msgId;
      pending.set(id, { resolve, reject });
      const go = () => ws.send(JSON.stringify({ id, method, params }));
      ready ? go() : onready.push(go);
    });
    const evalJS = async (expression) => {
      const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
      return r.result ? r.result.value : undefined;
    };
    resolve({ send, evalJS, ws });
  });
}

async function main() {
  const ver = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json/version`);
  const browser = await connect(ver.webSocketDebuggerUrl);
  const created = await browser.send('Target.createTarget', { url: 'about:blank' });
  let pt = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json`).then((l) => l.find((t) => t.id === created.targetId));
  let i = 0;
  while (!pt && i < 30) { await sleep(300); pt = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json`).then((l) => l.find((t) => t.id === created.targetId)); i++; }
  const p = await connect(pt.webSocketDebuggerUrl);
  await p.send('Page.enable');
  await p.send('Runtime.enable');

  const measure = async () => {
    const r = await p.send('Runtime.evaluate', {
      expression: `(() => ({
        innerW: window.innerWidth,
        sidebarW: document.getElementById('sidebar').offsetWidth,
        frameW: document.querySelector('.frame-wrap').offsetWidth,
        rightW: document.querySelector('.main-right').offsetWidth,
        scrollW: document.documentElement.scrollWidth,
        scrollH: document.documentElement.scrollHeight,
        innerH: window.innerHeight,
      }))()`,
      returnByValue: true,
    });
    return r.result.value;
  };

  const VIEWPORTS = [
    { w: 1920, h: 940, note: '大屏' },
    { w: 1366, h: 768, note: '常见笔记本' },
    { w: 1100, h: 700, note: '窄屏' },
    { w: 1024, h: 700, note: '紧凑' },
    { w: 754, h: 700, note: '极窄(上次诊断视口)' },
  ];

  let pass = true;
  for (const vp of VIEWPORTS) {
    await p.send('Emulation.setDeviceMetricsOverride', { width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: false });
    await p.send('Page.navigate', { url: URL });
    // 等 platform 初始化
    for (let k = 0; k < 40; k++) {
      const ok = await p.send('Runtime.evaluate', { expression: `document.getElementById('labelRows') !== null`, returnByValue: true });
      if (ok.result.value) break;
      await sleep(300);
    }
    await sleep(400); // 等 transition/布局稳定
    const m = await measure();
    const rightOk = m.rightW >= 320;
    const noPageScroll = m.scrollW <= m.innerW && m.scrollH <= m.innerH;
    if (!rightOk || !noPageScroll) pass = false;
    console.log(`[${vp.note} ${vp.w}px] 视口=${m.innerW} 侧栏=${m.sidebarW} 左栏/iframe=${m.frameW} 右栏=${m.rightW} ${rightOk ? '✅' : '❌<320'} | 无页面滚动=${noPageScroll ? '✅' : '❌'}`);
  }

  console.log(pass ? 'LAYOUT_CHECK_DONE' : 'LAYOUT_CHECK_FAIL');
  process.exit(pass ? 0 : 1);
}

main().catch((e) => {
  console.error('LAYOUT_CHECK_FAIL:', e.message);
  process.exit(1);
});
