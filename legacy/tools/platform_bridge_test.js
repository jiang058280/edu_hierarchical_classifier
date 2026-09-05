/**
 * 教育题目分层分类系统 - platform.html 端到端联动自测（CDP 驱动 headless Edge）
 * 场景：直接打开 http://127.0.0.1:7860/（智慧教研平台页），原 Gradio 分类器同源挂载于 /classifier
 * 验证验收标准核心链路（P1 阻断级 + 若干交互）：
 *   1. 根路径即平台页，iframe 加载 /classifier/，收到 edu-ready → 引导层隐藏、状态栏在线
 *   2. iframe 内真实分类 → 右侧「三级标签确认卡」同步真实学科/题型/知识点 + 置信度
 *   3. 摘要卡 / 分类历史（实时条）联动更新
 *   4. ✏️ 修正下拉 → 选择后标签即时更新（人工修正高于 AI）
 *   5. 动作按钮（存入题库 / 加入组卷篮）→ 成功弹窗
 *   6. 班级切换 → 已处理题目统计模拟变化
 *   7. 侧边栏折叠 → 宽度 64px
 *   8. 历史回看弹窗
 *   9. 100vh 无滚动
 * 用法：node tools/platform_bridge_test.js （需后端 7860 运行 + headless Edge CDP 9222）
 */
const CDP_PORT = 9222;
const PLATFORM_URL = 'http://127.0.0.1:7860/';

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
    const waitFor = async (expr, timeoutMs, desc) => {
      const start = Date.now();
      let last;
      while (Date.now() - start < timeoutMs) {
        try { last = await evalJS(expr); if (last) return last; } catch (_) {}
        await sleep(300);
      }
      throw new Error('等待超时: ' + desc + (last !== undefined ? ' (last=' + JSON.stringify(last) + ')' : ''));
    };
    resolve({ send, evalJS, waitFor, ws });
  });
}

// 在 platform 页内访问同源 iframe 的辅助表达式
const IF = `document.getElementById('eduFrame').contentWindow`;
const ifReady = `(function(){try{var w=${IF};return !!(w&&w.document&&w.document.getElementById('btnClassify'));}catch(e){return false;}})()`;
const ifNode = `(function(){try{return ${IF}.document.querySelector('#resultBody .tl-node')!==null;}catch(e){return false;}})()`;

async function main() {
  // 1. 创建 platform 页面 target（http://127.0.0.1:7860/）
  const ver = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json/version`);
  const browser = await connect(ver.webSocketDebuggerUrl);
  const created = await browser.send('Target.createTarget', { url: 'about:blank' });
  let pt = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json`).then((l) => l.find((t) => t.id === created.targetId));
  let i = 0;
  while (!pt && i < 30) { await sleep(300); pt = await jsonGet(`http://127.0.0.1:${CDP_PORT}/json`).then((l) => l.find((t) => t.id === created.targetId)); i++; }
  if (!pt) throw new Error('platform target 未就绪');
  const platform = await connect(pt.webSocketDebuggerUrl);
  await platform.send('Page.enable');
  await platform.send('Runtime.enable');
  await platform.send('Page.navigate', { url: PLATFORM_URL });
  await platform.waitFor(`document.getElementById('labelRows') !== null`, 15000, 'platform 页面 JS 初始化');

  // 2. 等 iframe(/classifier/) 内 Gradio 界面就绪（同源，直接经 contentWindow 访问）
  await platform.waitFor(ifReady, 25000, 'iframe 内 Gradio 界面就绪');

  // 3. 验收①：收到 edu-ready → 引导层隐藏、状态栏在线
  await platform.waitFor(`document.getElementById('backendOverlay').hidden === true`, 12000, '后端存活 → 引导层隐藏');
  const online = await platform.evalJS(`document.getElementById('stDot').classList.contains('online')`);
  console.log('[1] 存活检测: overlayHidden=true, dotOnline=' + online);

  // 4. 验收②（P1 核心）：iframe 内真实分类 → 右侧同步
  await platform.evalJS(`(function(){${IF}.document.querySelectorAll('#exampleGrid button')[1].click();})()`);
  await platform.evalJS(`(function(){${IF}.document.getElementById('btnClassify').click();})()`);
  await platform.waitFor(ifNode, 30000, 'iframe 分类结果渲染');
  await platform.waitFor(
    `document.querySelectorAll('#labelRows .lbl-text').length === 3 && document.querySelector('#labelRows .lbl-text').textContent !== '未分类'`,
    15000, 'platform 右侧三级标签同步',
  );
  const labels = await platform.evalJS(`Array.from(document.querySelectorAll('#labelRows .lbl-text')).map(function(e){return e.textContent;})`);
  const confs = await platform.evalJS(`Array.from(document.querySelectorAll('#labelRows .conf-num')).map(function(e){return e.textContent;})`);
  const summaryTxt = await platform.evalJS(`document.getElementById('sumBody').innerText`);
  const histLive = await platform.evalJS(`(document.querySelector('#histList .hist-item')||{}).innerText || ''`);
  const histCount = await platform.evalJS(`document.querySelectorAll('#histList .hist-item').length`);
  console.log('[2] 右侧标签:', JSON.stringify(labels), '| 置信度:', JSON.stringify(confs));
  console.log('[3] 摘要卡:', JSON.stringify(summaryTxt.replace(/\n/g, ' | ')));
  console.log('[4] 历史首条含实时:', histLive.includes('实时'), '| 历史条数:', histCount);
  if (labels.some((x) => !x || x === '未分类') || confs.length !== 3) throw new Error('右侧标签未同步真实结果');

  // 5. 验收③：修正下拉 → 选择后标签即时更新
  const fixBefore = await platform.evalJS(`document.querySelector('#labelRows .lbl-text').textContent`);
  await platform.evalJS(`document.querySelector('#labelRows .fix-btn').click()`);
  await platform.waitFor(`document.getElementById('fixMenu') && !document.getElementById('fixMenu').hidden`, 5000, '修正浮层弹出');
  const optCount = await platform.evalJS(`document.querySelectorAll('#fixMenu .fm-opt').length`);
  await platform.evalJS(`document.querySelector('#fixMenu .fm-opt').click()`);
  const fixAfter = await platform.evalJS(`document.querySelector('#labelRows .lbl-text').textContent`);
  const fixedBadge = await platform.evalJS(`document.querySelector('#labelRows .fixed-badge') !== null`);
  console.log('[5] 修正: 备选数=' + optCount + ', before=' + fixBefore + ' → after=' + fixAfter + ', 已修正徽标=' + fixedBadge);
  if (fixAfter === fixBefore || !fixedBadge) throw new Error('人工修正未生效');

  // 6. 验收④：动作按钮成功弹窗
  await platform.evalJS(`document.getElementById('btnSave').click()`);
  await platform.waitFor(`document.getElementById('dlg').open`, 5000, '存入题库弹窗');
  const dlgTitle = await platform.evalJS(`document.getElementById('dlgTitle').textContent`);
  await platform.evalJS(`document.getElementById('dlgOk').click()`);
  await platform.evalJS(`document.getElementById('btnBasket').click()`);
  await platform.waitFor(`document.getElementById('dlg').open`, 5000, '加入组卷篮弹窗');
  const dlgTitle2 = await platform.evalJS(`document.getElementById('dlgTitle').textContent`);
  await platform.evalJS(`document.getElementById('dlgOk').click()`);
  console.log('[6] 动作弹窗:', dlgTitle, '/', dlgTitle2);

  // 7. 验收⑤：班级切换 → 统计模拟变化
  const before = await platform.evalJS(`document.getElementById('stCount').textContent`);
  await platform.evalJS(`document.getElementById('selClass').value='初二(3)班'; document.getElementById('selClass').dispatchEvent(new Event('change'));`);
  const after = await platform.evalJS(`document.getElementById('stCount').textContent`);
  console.log('[7] 班级切换: stCount ' + before + ' → ' + after);
  if (after !== '1,052') throw new Error('班级切换统计未变化');

  // 8. 验收⑥：侧边栏折叠（等待 transition 完成）
  await platform.evalJS(`document.getElementById('btnFold').click()`);
  await sleep(400);
  const w1 = await platform.evalJS(`document.getElementById('sidebar').offsetWidth`);
  await platform.evalJS(`document.getElementById('btnFold').click()`);
  await sleep(400);
  const w2 = await platform.evalJS(`document.getElementById('sidebar').offsetWidth`);
  console.log('[8] 折叠宽度: ' + w1 + 'px / 展开 ' + w2 + 'px');
  if (w1 !== 64 || w2 !== 220) throw new Error('侧边栏折叠失败');

  // 9. 验收⑦：历史回看弹窗
  await platform.evalJS(`document.querySelectorAll('#histList .hist-item')[3].click()`);
  await platform.waitFor(`document.getElementById('dlgHist').open`, 5000, '历史详情弹窗');
  const histDlg = await platform.evalJS(`document.getElementById('dlgHistBody').innerText.slice(0, 40)`);
  await platform.evalJS(`document.getElementById('dlgHistOk').click()`);
  console.log('[9] 历史回看:', JSON.stringify(histDlg) + '…');

  // 10. 验收⑧：100vh 无滚动
  const sc = await platform.evalJS(`(() => ({ sh: document.documentElement.scrollHeight, ih: window.innerHeight, sw: document.documentElement.scrollWidth, iw: window.innerWidth }))()`);
  console.log('[10] 无滚动: scrollH=' + sc.sh + ' vs innerH=' + sc.ih + ', scrollW=' + sc.sw + ' vs innerW=' + sc.iw);
  if (sc.sh > sc.ih || sc.sw > sc.iw) throw new Error('页面出现滚动条');

  console.log('PLATFORM_E2E_DONE');
  process.exit(0);
}

main().catch((e) => {
  console.error('PLATFORM_E2E_FAIL:', e.message);
  process.exit(1);
});
