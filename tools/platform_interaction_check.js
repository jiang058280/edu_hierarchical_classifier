/**
 * platform.html 全量交互验证：
 *  心跳/引导层、通用弹窗关闭、历史弹窗关闭、遮罩关闭、navBank 折叠、修正浮层取消、通知面板、头像弹窗
 * 用法：node tools/platform_interaction_check.js （需后端 7860 + headless Edge CDP 9222）
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
  const evalJS = async (expression) => {
    const r = await p.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS 异常: ' + JSON.stringify(r.exceptionDetails));
    return r.result ? r.result.value : undefined;
  };
  // 等初始化
  for (let k = 0; k < 40; k++) {
    const ok = await evalJS(`document.getElementById('labelRows') !== null`);
    if (ok) break;
    await sleep(300);
  }

  let fail = 0;
  const check = (name, cond) => { console.log((cond ? '[✅] ' : '[❌] ') + name); if (!cond) fail++; };

  // 1. 心跳：等 stDot 变 online（收到 edu-ready）+ 引导层隐藏
  let dotOnline = false;
  for (let k = 0; k < 40; k++) {
    dotOnline = await evalJS(`document.getElementById('stDot').classList.contains('online')`);
    if (dotOnline) break;
    await sleep(500);
  }
  const overlayHidden = await evalJS(`document.getElementById('backendOverlay').hidden === true`);
  check('心跳 edu-ready → 状态在线、引导层隐藏 (dot=' + dotOnline + ', overlay=' + overlayHidden + ')', dotOnline && overlayHidden);

  // 0. 先在 iframe 内做一次真实分类，为修正浮层测试准备 lastResult
  const IF = `document.getElementById('eduFrame').contentWindow`;
  for (let k = 0; k < 40; k++) {
    const ok = await evalJS(`(function(){try{var w=${IF};return !!(w&&w.document&&w.document.getElementById('btnClassify'));}catch(e){return false;}})()`);
    if (ok) break;
    await sleep(500);
  }
  await evalJS(`(function(){${IF}.document.querySelectorAll('#exampleGrid button')[1].click();})()`);
  await evalJS(`(function(){${IF}.document.getElementById('btnClassify').click();})()`);
  for (let k = 0; k < 40; k++) {
    const ok = await evalJS(`(function(){try{return ${IF}.document.querySelector('#resultBody .tl-node')!==null;}catch(e){return false;}})()`);
    if (ok) break;
    await sleep(500);
  }
  for (let k = 0; k < 30; k++) {
    const ok = await evalJS(`document.querySelectorAll('#labelRows .lbl-text').length === 3 && document.querySelector('#labelRows .lbl-text').textContent !== '未分类'`);
    if (ok) break;
    await sleep(500);
  }
  console.log('[0] iframe 分类 → 右侧标签卡同步（前置准备）');

  // 2. 通用弹窗：btnSave 打开 → dlgOk 关闭
  await evalJS(`document.getElementById('btnSave').click()`);
  await sleep(300);
  const dlgOpen1 = await evalJS(`document.getElementById('dlg').open`);
  await evalJS(`document.getElementById('dlgOk').click()`);
  await sleep(300);
  const dlgClosed1 = await evalJS(`document.getElementById('dlg').open === false`);
  check('通用弹窗"知道了"可关闭 (open=' + dlgOpen1 + ' → closed=' + dlgClosed1 + ')', dlgOpen1 && dlgClosed1);

  // 3. 通用弹窗：btnAvatar 打开 → 遮罩点击关闭（点击 dialog 自身 = 点 backdrop）
  await evalJS(`document.getElementById('btnAvatar').click()`);
  await sleep(300);
  const dlgOpen2 = await evalJS(`document.getElementById('dlg').open`);
  await evalJS(`(function(){ var d=document.getElementById('dlg'); var el=document.elementFromPoint(30,30)||d; if(el===d){el.click();} else {d.dispatchEvent(new MouseEvent('click',{bubbles:true}));} })()`);
  await sleep(300);
  const dlgClosed2 = await evalJS(`document.getElementById('dlg').open === false`);
  check('通用弹窗遮罩点击可关闭 (open=' + dlgOpen2 + ' → closed=' + dlgClosed2 + ')', dlgOpen2 && dlgClosed2);

  // 4. 历史弹窗：关闭按钮 + 遮罩
  await evalJS(`document.querySelectorAll('#histList .hist-item')[0].click()`);
  await sleep(300);
  const hOpen = await evalJS(`document.getElementById('dlgHist').open`);
  await evalJS(`document.getElementById('dlgHistOk').click()`);
  await sleep(300);
  const hClosed = await evalJS(`document.getElementById('dlgHist').open === false`);
  check('历史弹窗"关闭"可关闭 (open=' + hOpen + ' → closed=' + hClosed + ')', hOpen && hClosed);

  // 5. navBank 题库管理：点击折叠/展开子菜单
  const openBefore = await evalJS(`document.getElementById('navBank').parentElement.classList.contains('open')`);
  await evalJS(`document.getElementById('navBank').click()`);
  await sleep(300);
  const openAfter1 = await evalJS(`document.getElementById('navBank').parentElement.classList.contains('open')`);
  await evalJS(`document.getElementById('navBank').click()`);
  await sleep(300);
  const openAfter2 = await evalJS(`document.getElementById('navBank').parentElement.classList.contains('open')`);
  check('题库管理可折叠/展开 (open=' + openBefore + '→' + openAfter1 + '→' + openAfter2 + ')', openAfter1 !== openAfter2);

  // 6. 修正浮层：打开 + 取消关闭
  await evalJS(`document.querySelector('#labelRows .fix-btn').click()`);
  await sleep(300);
  const fixOpen = await evalJS(`document.getElementById('fixMenu').hidden === false`);
  await evalJS(`document.querySelector('#fixMenu .fm-cancel').click()`);
  await sleep(300);
  const fixClosed = await evalJS(`document.getElementById('fixMenu').hidden === true`);
  check('修正浮层可打开/取消关闭 (open=' + fixOpen + ' → closed=' + fixClosed + ')', fixOpen && fixClosed);

  // 7. 通知面板打开/关闭
  await evalJS(`document.getElementById('btnBell').click()`);
  await sleep(300);
  const npOpen = await evalJS(`document.getElementById('notifyPanel').hidden === false`);
  await evalJS(`document.getElementById('btnBell').click()`);
  await sleep(300);
  const npClosed = await evalJS(`document.getElementById('notifyPanel').hidden === true`);
  check('通知面板可开/关 (open=' + npOpen + ' → closed=' + npClosed + ')', npOpen && npClosed);

  // 8. 侧边栏折叠
  await evalJS(`document.getElementById('btnFold').click()`);
  await sleep(400);
  const w1 = await evalJS(`document.getElementById('sidebar').offsetWidth`);
  check('侧边栏折叠到 64px (=' + w1 + ')', w1 === 64);
  await evalJS(`document.getElementById('btnFold').click()`);
  await sleep(400);

  console.log(fail ? `INTERACTION_CHECK_FAIL (${fail} 项失败)` : 'INTERACTION_CHECK_DONE');
  process.exit(fail ? 1 : 0);
}
main().catch((e) => { console.error('INTERACTION_CHECK_FAIL:', e.message); process.exit(1); });
