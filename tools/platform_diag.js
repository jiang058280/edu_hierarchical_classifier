/**
 * 教育题目分层分类系统 - http://127.0.0.1:7860/ 平台页渲染诊断
 * 采集：页面结构完整性、iframe 加载状态、布局尺寸、控制台报错
 * 用法：node tools/platform_diag.js （需 7860 运行 + headless Edge CDP 9222）
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
    const send = (method, params) => {
      return new Promise((resolve, reject) => {
        const id = ++msgId;
        pending.set(id, { resolve, reject });
        const go = () => ws.send(JSON.stringify({ id, method, params }));
        ready ? go() : onready.push(go);
      });
    };
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

  // 收集控制台错误与页面异常
  const errors = [];
  p.ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      errors.push(msg.params.args.map((a) => a.value || a.description || '').join(' '));
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      errors.push('EXC: ' + (msg.params.exceptionDetails.text || '') + ' ' + (msg.params.exceptionDetails.exception ? msg.params.exceptionDetails.exception.description || '' : ''));
    }
    if (msg.id && pending2.has(msg.id)) {
      const pp = pending2.get(msg.id);
      pending2.delete(msg.id);
      msg.error ? pp.reject(new Error(msg.error.message)) : pp.resolve(msg.result);
    }
  };
  const pending2 = new Map();
  let mid = 0;
  const send2 = (method, params) => new Promise((resolve, reject) => { const id = ++mid; pending2.set(id, { resolve, reject }); p.ws.send(JSON.stringify({ id, method, params })); });
  const evalJS = async (expression) => {
    const r = await send2('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    return r.result ? r.result.value : undefined;
  };
  const waitFor = async (expr, timeoutMs, desc) => {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      try { const v = await evalJS(expr); if (v) return v; } catch (_) {}
      await sleep(300);
    }
    throw new Error('等待超时: ' + desc);
  };

  await send2('Page.navigate', { url: URL });
  await waitFor(`document.getElementById('labelRows') !== null`, 15000, 'platform 初始化');

  // 结构完整性
  const struct = await evalJS(`(() => {
    var g = function(id){ return document.getElementById(id); };
    return {
      hasSidebar: !!g('sidebar'),
      hasTopbar: !!document.querySelector('.topbar'),
      hasFrame: !!g('eduFrame'),
      hasRight: !!document.querySelector('.main-right'),
      hasSum: !!g('sumBody'), hasLabels: !!g('labelRows'),
      hasActions: !!g('btnSave') && !!g('btnBasket') && !!g('btnReclassify'),
      hasHist: !!g('histList'), hasStatusbar: !!document.querySelector('.statusbar'),
      iframeSrc: g('eduFrame') ? g('eduFrame').src : '(无)',
      overlayHidden: g('backendOverlay') ? g('backendOverlay').hidden : null,
    };
  })()`);
  console.log('[结构]', JSON.stringify(struct));

  // 布局尺寸
  const layout = await evalJS(`(() => {
    var g = function(id){ return document.getElementById(id); };
    return {
      innerW: window.innerWidth, innerH: window.innerHeight,
      scrollH: document.documentElement.scrollHeight,
      scrollW: document.documentElement.scrollWidth,
      sidebarW: g('sidebar') ? g('sidebar').offsetWidth : -1,
      frameW: document.querySelector('.frame-wrap') ? document.querySelector('.frame-wrap').offsetWidth : -1,
      rightW: document.querySelector('.main-right') ? document.querySelector('.main-right').offsetWidth : -1,
      topbarH: document.querySelector('.topbar') ? document.querySelector('.topbar').offsetHeight : -1,
      statusbarH: document.querySelector('.statusbar') ? document.querySelector('.statusbar').offsetHeight : -1,
      bodyFont: getComputedStyle(document.body).fontSize,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      overflow: getComputedStyle(document.body).overflow,
    };
  })()`);
  console.log('[布局]', JSON.stringify(layout));

  // iframe 加载状态（等就绪）
  const iframeStatus = await (async () => {
    for (let k = 0; k < 30; k++) {
      const s = await evalJS(`(function(){ try { var w=document.getElementById('eduFrame').contentWindow; if (w && w.document && w.document.getElementById('btnClassify')) return { ready: true }; return { ready: false, state: w && w.document ? w.document.readyState : 'no-doc' }; } catch(e){ return { ready:false, err: e.message }; } })()`);
      if (s.ready) return s;
      await sleep(500);
    }
    return { ready: false, timeout: true };
  })();
  console.log('[iframe]', JSON.stringify(iframeStatus));

  // iframe 内布局
  const iframeLayout = await evalJS(`(function(){ try {
    var w = document.getElementById('eduFrame').contentWindow;
    var d = w.document;
    return {
      innerW: w.innerWidth, innerH: w.innerHeight,
      scrollH: d.documentElement.scrollHeight,
      scrollW: d.documentElement.scrollWidth,
      btnClassify: !!d.getElementById('btnClassify'),
      exampleCount: d.querySelectorAll('#exampleGrid button').length,
    };
  } catch(e){ return { err: e.message }; } })()`);
  console.log('[iframe布局]', JSON.stringify(iframeLayout));

  await sleep(800);
  console.log('[控制台错误]', errors.length ? errors.slice(0, 5) : '无');

  console.log('PLATFORM_DIAG_DONE');
  process.exit(0);
}

main().catch((e) => {
  console.error('PLATFORM_DIAG_FAIL:', e.message);
  process.exit(1);
});
