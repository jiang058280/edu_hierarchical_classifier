/**
 * 心跳链路诊断：检查 iframe 是否真正加载 Gradio 界面、内部心跳是否发出、父页面是否收到
 * 用法：node tools/platform_heartbeat_diag.js （需后端 7860 + headless Edge CDP 9222）
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
    ws.onerror = (e) => reject(new Error('WS 失败: ' + e.message));
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
    resolve({ send, ws });
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
  await p.send('Page.navigate', { url: URL });

  // 注入一个父页面消息探针（在已有监听之外额外记录）
  await p.send('Runtime.evaluate', {
    expression: `window.__msgProbe = []; window.addEventListener('message', function(e){ if(e.data && e.data.type) window.__msgProbe.push(e.data.type); });`,
    returnByValue: true,
  });

  const evalJS = async (expression) => {
    const r = await p.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    return r.result ? r.result.value : undefined;
  };

  // 等 platform 初始化 + iframe 尝试加载
  for (let k = 0; k < 40; k++) {
    const ok = await evalJS(`document.getElementById('labelRows') !== null`);
    if (ok) break;
    await sleep(300);
  }
  await sleep(1000);

  // 等待 12s 观察心跳（5s 一次，应收到 ≥2 次）
  const probeBefore = await evalJS(`window.__msgProbe.slice()`);
  await sleep(12000);
  const probeAfter = await evalJS(`window.__msgProbe.slice()`);
  const received = probeAfter.filter((t) => t === 'edu-ready').length - probeBefore.filter((t) => t === 'edu-ready').length;
  console.log('[心跳] 12s 内收到 edu-ready 次数:', received, '| 全部消息:', JSON.stringify(probeAfter));

  // iframe 状态
  const iframe = await evalJS(`(() => {
    var f = document.getElementById('eduFrame');
    var out = { src: f ? f.src : '(无)', readyState: 'n/a' };
    try {
      var w = f.contentWindow;
      if (!w || !w.document) { out.readyState = '无 contentWindow'; return out; }
      out.readyState = w.document.readyState;
      out.hasBtnClassify = !!w.document.getElementById('btnClassify');
      out.hasResultBody = !!w.document.getElementById('resultBody');
      out.hasExampleGrid = !!w.document.getElementById('exampleGrid');
      out.title = w.document.title;
      out.bodySnippet = (w.document.body ? w.document.body.innerHTML : '').slice(0, 120);
    } catch (e) { out.err = e.message; }
    return out;
  })()`);
  console.log('[iframe]', JSON.stringify(iframe, null, 1));

  // overlay 状态
  console.log('[overlay] hidden=', await evalJS(`document.getElementById('backendOverlay').hidden`));

  console.log('HEARTBEAT_DIAG_DONE');
  process.exit(0);
}
main().catch((e) => { console.error('HEARTBEAT_DIAG_FAIL:', e.message); process.exit(1); });
