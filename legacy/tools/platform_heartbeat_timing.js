/**
 * 心跳时序诊断：测量页面打开后首次收到 edu-ready 的时间，
 * 验证心跳独立于 initApp（iframe 脚本一加载即发送，而非等待界面渲染）
 * 用法：node tools/platform_heartbeat_timing.js （需后端 7860 + headless Edge CDP 9222）
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

  const evalJS = async (expression) => {
    const r = await p.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    return r.result ? r.result.value : undefined;
  };

  // 先导航到空白页，注入探针，再导航到平台页（确保从零计时）
  await p.send('Page.navigate', { url: 'about:blank' });
  await sleep(300);
  await p.send('Runtime.evaluate', {
    expression: `window.__t0 = Date.now(); window.__firstReady = null; window.addEventListener('message', function(e){ if(e.data && e.data.type === 'edu-ready' && !window.__firstReady) window.__firstReady = Date.now() - window.__t0; });`,
    returnByValue: true,
  });
  await p.send('Page.navigate', { url: URL });

  // 轮询等待平台初始化 + 首次心跳
  let firstReady = null;
  for (let k = 0; k < 40; k++) {
    firstReady = await evalJS(`window.__firstReady`);
    if (firstReady !== null) break;
    const ready = await evalJS(`document.getElementById('labelRows') !== null`);
    if (ready) await sleep(200);
    await sleep(200);
  }
  console.log('[首次心跳] 页面打开后约', firstReady, 'ms 收到 edu-ready');

  // 等引导层/状态
  let dotOnline = false;
  for (let k = 0; k < 30; k++) {
    dotOnline = await evalJS(`document.getElementById('stDot').classList.contains('online')`);
    if (dotOnline) break;
    await sleep(300);
  }
  const overlayHidden = await evalJS(`document.getElementById('backendOverlay').hidden === true`);
  console.log('[状态] dotOnline=' + dotOnline + ', overlayHidden=' + overlayHidden);
  if (!dotOnline || !overlayHidden) throw new Error('心跳后状态未在线');

  // iframe 是否就绪（对照）
  const iframe = await evalJS(`(function(){try{var w=document.getElementById('eduFrame').contentWindow;return {readyState:w.document.readyState, hasBtn:w.document.getElementById('btnClassify')!==null};}catch(e){return {err:e.message};}})()`);
  console.log('[iframe]', JSON.stringify(iframe));

  console.log('HEARTBEAT_TIMING_DONE');
  process.exit(0);
}
main().catch((e) => { console.error('HEARTBEAT_TIMING_FAIL:', e.message); process.exit(1); });
