/**
 * 验证：历史详情弹窗"关闭"按钮（dlgHistOk）点击后能正常关闭 dialog
 * 用法：node tools/platform_close_check.js （需后端 7860 + headless Edge CDP 9222）
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
  await p.send('Page.navigate', { url: URL });
  for (let k = 0; k < 40; k++) {
    const ok = await p.send('Runtime.evaluate', { expression: `document.getElementById('labelRows') !== null`, returnByValue: true });
    if (ok.result.value) break;
    await sleep(300);
  }
  const evalJS = async (expression) => {
    const r = await p.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    return r.result ? r.result.value : undefined;
  };
  await sleep(400);

  // 打开历史详情弹窗
  await evalJS(`document.querySelectorAll('#histList .hist-item')[0].click()`);
  await sleep(300);
  const openBefore = await evalJS(`document.getElementById('dlgHist').open`);
  console.log('[打开] 弹窗 open=' + openBefore);
  if (!openBefore) throw new Error('历史弹窗未打开');

  // 点击"关闭"按钮
  await evalJS(`document.getElementById('dlgHistOk').click()`);
  await sleep(300);
  const openAfter = await evalJS(`document.getElementById('dlgHist').open`);
  console.log('[关闭] 点击"关闭"后 open=' + openAfter);
  if (openAfter) throw new Error('关闭按钮无效：弹窗仍未关闭');

  console.log('CLOSE_CHECK_DONE');
  process.exit(0);
}
main().catch((e) => { console.error('CLOSE_CHECK_FAIL:', e.message); process.exit(1); });
