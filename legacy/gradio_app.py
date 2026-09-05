# -*- coding: utf-8 -*-
r"""
教育题目分层分类系统 - Gradio Web 交互界面（新版界面 + 真实后端桥接）

界面：完全采用新版无滚动一屏界面（ui_preview.html 设计）——纯 HTML 渲染，
      由 launch(head=...) 注入全局 JS 与交互。
后端：通过 Gradio REST API（/gradio_api/api/classify 等）桥接——
      “开始分类”调用真实模型预测，反馈写入 feedback.jsonl 并累计计数，
      统计持久化到 logs/stats.json。

布局硬约束：100vh 无滚动、导航 50px → 主区 flex:1 → 页脚 28px、左 68% / 右 32%。

运行：venv\Scripts\python app.py  →  http://127.0.0.1:7860
"""
import os
import sys
import json
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from utils import get_project_root, setup_environment

ROOT = get_project_root()
setup_environment()

import gradio as gr

from predict import load_predictor

# ---------- 置信度分级（>90% 绿 / 70-90% 橙 / <70% 红） ----------
HIGH, LOW = 0.9, 0.7
C_HIGH, C_MID, C_LOW = "#22C55E", "#FFB74D", "#EF4444"
REVIEW_THRESHOLD = LOW

STATS_PATH = os.path.join(ROOT, "logs", "stats.json")
COUNTS_PATH = os.path.join(ROOT, "logs", "feedback_counts.json")

_predictor = None


def get_predictor():
    """懒加载全局预测器（首次调用时加载模型，较慢）"""
    global _predictor
    if _predictor is None:
        _predictor = load_predictor()
    return _predictor


def conf_color(conf: float) -> str:
    """按置信度返回颜色：≥0.9 绿 / 0.7~0.9 橙 / <0.7 红"""
    if conf >= HIGH:
        return C_HIGH
    if conf >= LOW:
        return C_MID
    return C_LOW


# ================= 新版结果渲染（匹配 ui_preview.html 的 CSS 类名） =================
RING_R = 17
RING_C = 2 * 3.141592653589793 * RING_R


def ring_svg(conf: float, label: str) -> str:
    """新版迷你圆环（45px）：轨道 + 语义色前景环 + 中心百分比（JS 从 0 递增）"""
    pct = min(1.0, conf) * 100
    color = conf_color(conf)
    offset = RING_C * (1 - min(1.0, conf))
    return f'''<div class="mini-ring-wrap">
<svg class="mini-ring" width="45" height="45" viewBox="0 0 45 45" aria-label="{label}置信度">
  <circle cx="22.5" cy="22.5" r="{RING_R}" stroke="#E5E9F5" stroke-width="5" fill="none"/>
  <circle class="ring-fg" cx="22.5" cy="22.5" r="{RING_R}" stroke="{color}" stroke-width="5" fill="none"
          stroke-dasharray="{RING_C:.2f}" stroke-dashoffset="{RING_C:.2f}"
          stroke-linecap="round" transform="rotate(-90 22.5 22.5)" data-offset="{offset:.2f}"/>
</svg>
<span class="ring-num" data-target="{pct:.0f}" data-color="{color}">0%</span>
</div>'''


def timeline_node(dot_cls: str, label: str, value: str, conf: float) -> str:
    """时间轴单个节点：彩色圆点 + 标签 + 迷你圆环 + 具体值"""
    return f'''<div class="tl-node">
  <div class="tl-head"><span class="tl-dot {dot_cls}"></span><span class="tl-label">{label}</span></div>
  {ring_svg(conf, label)}
  <div class="tl-value">{value}</div>
</div>'''


def build_result_html(result: dict) -> str:
    """组装新版分类结果：低置信提示 + 学科 → 题型 → 知识点 时间轴"""
    conf = result["confidence"]
    nodes = [
        timeline_node("blue", "学科", result["subject"], conf["subject"]),
        timeline_node("orange", "题型", result["question_type"], conf["question_type"]),
        timeline_node("cyan", "知识点", result["knowledge_point"], conf["knowledge_point"]),
    ]
    timeline = nodes[0] + '<div class="tl-line"></div>' + nodes[1] + '<div class="tl-line"></div>' + nodes[2]
    warn = ""
    if min(conf.values()) < REVIEW_THRESHOLD:
        warn = ('<div class="warn-banner"><span class="warn-mark">!</span>'
                '<span>部分分类结果置信度较低（&lt;70%），建议人工复核</span></div>')
    return f'<div class="result-wrap">{warn}<div class="timeline">{timeline}</div></div>'


PLACEHOLDER_HTML = '<div class="result-empty">⏳ 等待分类...</div>'


def error_html(msg: str) -> str:
    return f'<div class="result-empty result-error">⚠️ 分类失败：{msg}</div>'


# ================= 统计与反馈计数（持久化） =================
def _today() -> str:
    return datetime.date.today().isoformat()


def _empty_stats() -> dict:
    return {"date": _today(), "count": 0, "total_conf": 0.0, "subjects": {}}


def load_stats() -> dict:
    """读取今日统计（不更新）；跨日则重置"""
    if not os.path.exists(STATS_PATH):
        return _empty_stats()
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            stats = json.load(f)
        if stats.get("date") != _today():
            return _empty_stats()
        stats.setdefault("count", 0)
        stats.setdefault("total_conf", 0.0)
        stats.setdefault("subjects", {})
        return stats
    except Exception:
        return _empty_stats()


def update_stats(result: dict) -> dict:
    """根据一次分类结果更新并持久化今日统计，返回更新后的 stats"""
    stats = load_stats()
    conf = result["confidence"]
    avg_conf = (conf["subject"] + conf["question_type"] + conf["knowledge_point"]) / 3
    stats["date"] = _today()
    stats["count"] = int(stats.get("count", 0)) + 1
    stats["total_conf"] = float(stats.get("total_conf", 0.0)) + avg_conf
    subj = result["subject"]
    stats["subjects"] = stats.get("subjects", {})
    stats["subjects"][subj] = stats["subjects"].get(subj, 0) + 1
    try:
        os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[app] 统计写入失败：{e}")
    return stats


def load_counts() -> dict:
    """读取累计反馈计数（正确/错误），缺失或异常时返回初始 0"""
    if not os.path.exists(COUNTS_PATH):
        return {"ok": 0, "bad": 0}
    try:
        with open(COUNTS_PATH, encoding="utf-8") as f:
            counts = json.load(f)
        counts.setdefault("ok", 0)
        counts.setdefault("bad", 0)
        return counts
    except Exception:
        return {"ok": 0, "bad": 0}


def save_counts(counts: dict):
    """持久化累计反馈计数"""
    try:
        os.makedirs(os.path.dirname(COUNTS_PATH), exist_ok=True)
        with open(COUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(counts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[app] 反馈计数写入失败：{e}")


# ================= REST 后端函数（供新版界面 JS fetch 调用） =================
def classify(text: str):
    """开始分类 → [结果HTML, 今日分类数, 平均置信度%]。失败/空输入返回占位与当前统计"""
    stats = load_stats()
    count = int(stats.get("count", 0))
    avg = (stats["total_conf"] / count * 100) if count > 0 else 0
    if not text or not text.strip():
        return PLACEHOLDER_HTML, count, round(avg, 1)
    try:
        result = get_predictor().predict(text)
    except Exception as e:
        return error_html(str(e)), count, round(avg, 1)
    stats = update_stats(result)
    count = int(stats["count"])
    avg = stats["total_conf"] / count * 100
    return build_result_html(result), count, round(avg, 1)


def submit_feedback(text: str, is_correct: bool):
    """记录反馈到 logs/feedback.jsonl 并累计计数 → [正确数, 错误数]"""
    path = os.path.join(ROOT, "logs", "feedback.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {
            "time": datetime.datetime.now().isoformat(),
            "text": text,
            "result_correct": bool(is_correct),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[app] 反馈写入失败：{e}")
    counts = load_counts()
    counts["ok" if is_correct else "bad"] += 1
    save_counts(counts)
    return counts["ok"], counts["bad"]


def initial_ui_values() -> dict:
    """页面初始渲染值：今日统计 + 反馈计数"""
    stats = load_stats()
    count = int(stats.get("count", 0))
    avg = (stats["total_conf"] / count * 100) if count > 0 else 0
    counts = load_counts()
    return {
        "count": count,
        "avg": ("--" if count == 0 else f"{avg:.1f}"),
        "ok": counts.get("ok", 0),
        "bad": counts.get("bad", 0),
    }


# ================= 新版界面 HTML（纯结构，无 script——由 head 注入 JS） =================
def render_ui_html() -> str:
    v = initial_ui_values()
    return f'''
<div class="app">
  <header class="navbar">
    <div class="nav-left">
      <span class="nav-logo">🏫</span>
      <span class="nav-title">教育题目分层分类</span>
    </div>
    <div class="nav-right">
      <button class="btn-history" id="btnHistory">📋 历史记录</button>
    </div>
  </header>

  <main class="main">
    <section class="left">
      <div class="card input-card">
        <label class="lbl" for="inputText">输入题目</label>
        <textarea id="inputText" placeholder="粘贴或输入题目文本..."></textarea>
        <label class="lbl mt" for="exampleGrid">快捷示例</label>
        <div class="example-grid" id="exampleGrid"></div>
        <div class="action-row">
          <button class="btn-go" id="btnClassify">🚀 开始分类</button>
          <button class="btn-clear" id="btnClear">清空</button>
        </div>
      </div>

      <div class="card result-card">
        <div class="card-title">分类结果</div>
        <div class="result-body" id="resultBody">{PLACEHOLDER_HTML}</div>
      </div>
    </section>

    <section class="right">
      <div class="card feedback-card">
        <div class="card-title">📌 结果反馈</div>
        <div class="fb-btns">
          <button class="fb-btn" id="fbOk"><span class="fb-ic">✅</span><span>正确</span></button>
          <button class="fb-btn" id="fbBad"><span class="fb-ic">❌</span><span>错误</span></button>
        </div>
        <div class="fb-counts">
          正确 <b class="ok-num" id="countOk">{v['ok']}</b> · 错误 <b class="bad-num" id="countBad">{v['bad']}</b>
        </div>
      </div>

      <div class="card stats-card">
        <div class="card-title">📊 今日统计</div>
        <div class="stats-row">
          <div class="stat-row">
            <span class="stat-label">📈 分类数</span>
            <span class="stat-value" id="statCount">{v['count']}</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">🎯 置信度</span>
            <span class="stat-value"><span id="statConf">{v['avg']}</span><span class="pct">%</span></span>
          </div>
          <div class="stat-row">
            <span class="stat-label">📚 学科</span>
            <div class="subj-dots">
              <span class="dot-s">语</span><span class="dot-m">数</span><span class="dot-e">英</span>
              <span style="background:linear-gradient(135deg,#6EE7B7,#10B981)">物</span>
              <span style="background:linear-gradient(135deg,#C4B5FD,#8B5CF6)">化</span>
              <span style="background:linear-gradient(135deg,#86EFAC,#22C55E)">生</span>
              <span style="background:linear-gradient(135deg,#FCA5A5,#EF4444)">史</span>
              <span style="background:linear-gradient(135deg,#93C5FD,#3B82F6)">地</span>
              <span style="background:linear-gradient(135deg,#FCD34D,#F59E0B)">政</span>
            </div>
          </div>
        </div>
      </div>

      <div class="card history-card">
        <div class="card-title">📜 分类历史</div>
        <div class="history-list" id="historyList">
          <div class="history-empty">暂无分类记录，开始分类后这里会显示历史</div>
        </div>
        <div class="history-clear" id="historyClear" style="display:none;">
          <button id="btnClearHistory">🗑️ 清空历史</button>
        </div>
      </div>
    </section>

  <footer class="footer">© 2026 教育题分层分类系统 · 数据仅用于教育研究</footer>
</div>
<div id="toast"></div>
'''


# ================= 新版界面全局样式（含 Gradio 容器覆盖，保证无滚动一屏） =================
UI_CSS = """
/* ===== Gradio 容器覆盖：全屏、无 padding、无滚动 ===== */
html, body, .gradio-app, .gradio-container, .gradio-root {
  overflow: hidden !important;
  height: 100% !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0 !important;
  max-width: none !important;
  background: radial-gradient(1200px 800px at 20% 10%, #F5F7FF 0%, #DDE3F0 100%) !important;
  background-color: #F5F7FF !important;
}
.gradio-container {
  max-width: none !important;
  width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}
/* gr.HTML 组件的容器（精准选择器，避免影响 Gradio 内部） */
.gradio-container .html-container,
.gradio-container .gr-html {
  padding: 0 !important;
  margin: 0 !important;
  max-width: 100% !important;
  width: 100% !important;
}
.gradio-container .block-container,
.gradio-container .gr-block-container {
  padding: 0 !important;
  margin: 0 !important;
  max-width: none !important;
  width: 100% !important;
}
footer, .gradio-container footer, #footer { display: none !important; visibility: hidden !important; }

/* ===== 全局 ===== */
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "SF Pro Display",
               "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  color: #1A1A2E;
  -webkit-font-smoothing: antialiased;
}

/* ===== 全屏 Flex 列骨架：导航 50 → 主区 flex:1 → 页脚 28 ===== */
.app {
  height: 100vh !important;
  width: 100% !important;
  max-width: none !important;
  display: flex !important;
  flex-direction: column !important;
  overflow: hidden !important;
}
/* Gradio #app 容器也需要占满 */
#app, .gradio-app > div {
  width: 100% !important;
  max-width: none !important;
  padding: 0 !important;
  margin: 0 !important;
}
.navbar {
  flex: 0 0 50px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  padding: 0 20px !important;
  background: rgba(255, 255, 255, 0.55) !important;
  backdrop-filter: blur(12px) !important;
  -webkit-backdrop-filter: blur(12px) !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.85) !important;
}
.nav-left { display: flex; align-items: center; gap: 10px; }
.nav-logo { font-size: 20px; line-height: 1; }
.nav-title { font-size: 18px; font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; }
.btn-history {
  height: 32px; padding: 0 16px; border-radius: 16px;
  border: 1px solid rgba(91, 111, 245, 0.35);
  background: transparent; color: #5B6FF5; font-size: 13px; font-weight: 600;
  cursor: pointer; transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
  white-space: nowrap;
}
.btn-history:hover { transform: translateY(-1px); background: rgba(91,111,245,0.08); box-shadow: 0 4px 12px rgba(91,111,245,0.16); }

.main {
  flex: 1 !important; min-height: 0 !important;
  display: block !important; padding: 12px !important; overflow: auto !important;
}
/* 只针对 .app 内部的 .main 应用网格布局，避免影响 Gradio 外层 .main */
.app .main {
  display: grid !important; grid-template-columns: 60% 40% !important; gap: 12px !important;
  align-items: stretch !important; justify-items: stretch !important;
}
.app .left  { width: 100% !important; min-width: 0 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; gap: 12px !important; overflow-y: auto !important; overflow-x: hidden !important; padding-right: 4px !important; }
.app .right { width: 100% !important; min-width: 0 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; gap: 12px !important; overflow-y: auto !important; overflow-x: hidden !important; padding-right: 4px !important; }
/* ===== 全局通用滚动条（左右栏共用一套样式） ===== */
.app .left::-webkit-scrollbar,
.app .right::-webkit-scrollbar { width: 6px; }
.app .left::-webkit-scrollbar-track,
.app .right::-webkit-scrollbar-track { background: rgba(91,111,245,0.04); border-radius: 3px; }
.app .left::-webkit-scrollbar-thumb,
.app .right::-webkit-scrollbar-thumb { background: rgba(91,111,245,0.25); border-radius: 3px; }
.app .left::-webkit-scrollbar-thumb:hover,
.app .right::-webkit-scrollbar-thumb:hover { background: rgba(91,111,245,0.4); }

.app .card {
  width: 100% !important;
  background: rgba(255, 255, 255, 0.6) !important;
  backdrop-filter: blur(12px) !important;
  -webkit-backdrop-filter: blur(12px) !important;
  border: 1px solid rgba(255, 255, 255, 0.75) !important;
  border-radius: 16px !important;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08) !important;
  padding: 14px !important;
  flex: 0 0 auto !important;
}
.card-title { font-size: 14px; font-weight: 700; color: #1A1A2E; margin-bottom: 10px; white-space: nowrap; }
.lbl { display: block; font-size: 11px; font-weight: 700; color: #4A4A6A; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lbl.mt { margin-top: 10px; }

/* 输入 */
.input-card { flex: 0 0 auto; }
textarea#inputText {
  width: 100%; height: 120px; resize: vertical;
  border: 1px solid #E0E4F0; border-radius: 12px;
  padding: 12px 14px; font-size: 14px; font-family: inherit; line-height: 1.6;
  background: rgba(255, 255, 255, 0.85); color: #1A1A2E;
  box-shadow: inset 0 2px 6px rgba(26, 26, 46, 0.04);
  outline: none; transition: border-color 0.2s ease, box-shadow 0.2s ease;
  white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word;
}
textarea#inputText:focus { border-color: #5B6FF5; box-shadow: 0 0 0 3px rgba(91,111,245,0.14), inset 0 2px 6px rgba(26,26,46,0.03); }
textarea#inputText::placeholder { color: #A0A8B8; }

/* 快捷示例 3×3 */
.example-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.example-grid button {
  height: 32px; border-radius: 16px;
  border: 1px solid rgba(91, 111, 245, 0.22);
  background: rgba(255, 255, 255, 0.72);
  color: #4A4A6A; font-size: 13px; font-weight: 500; cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease, border-color 0.2s ease;
  white-space: nowrap;
}
.example-grid button:hover {
  transform: translateY(-2px);
  background: rgba(91, 111, 245, 0.1); border-color: #5B6FF5; color: #5B6FF5;
  box-shadow: 0 4px 12px rgba(91, 111, 245, 0.16);
}
.example-grid button:active { transform: translateY(0); }

/* 操作按钮 */
.action-row { display: flex; gap: 8px; margin-top: 8px; }
.btn-go {
  flex: 3; height: 40px; border: none; border-radius: 8px;
  background: linear-gradient(135deg, #5B6FF5, #7B8FFA);
  color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
  box-shadow: 0 4px 12px rgba(91, 111, 245, 0.3);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  white-space: nowrap;
}
.btn-go:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(91,111,245,0.42); }
.btn-go:active { transform: translateY(0); }
.btn-clear {
  flex: 1; height: 40px; border-radius: 8px;
  border: 1px solid rgba(91, 111, 245, 0.35);
  background: transparent; color: #5B6FF5; font-size: 13px; font-weight: 500; cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
  white-space: nowrap;
}
.btn-clear:hover { transform: translateY(-2px); background: rgba(91,111,245,0.06); box-shadow: 0 4px 12px rgba(91,111,245,0.12); }
.btn-clear:active { transform: translateY(0); }

/* 分类结果区 */
.result-card { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.result-body { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
.result-wrap { width: 100%; display: flex; flex-direction: column; align-items: center; }
.result-empty { color: #A0A8B8; font-size: 13px; letter-spacing: 0.03em; }
.result-error { color: #B91C1C; font-size: 13px; line-height: 1.6; text-align: center; }
.result-loading { display: flex; align-items: center; gap: 5px; color: #A0A8B8; font-size: 13px; }
.ldot { width: 7px; height: 7px; border-radius: 50%; background: linear-gradient(135deg, #7B8FFA, #4A5CF7); animation: ldot 1s ease-in-out infinite; }
.ldot:nth-child(2) { animation-delay: 0.16s; }
.ldot:nth-child(3) { animation-delay: 0.32s; }
@keyframes ldot { 0%, 60%, 100% { transform: translateY(0); opacity: 0.35; } 30% { transform: translateY(-8px); opacity: 1; } }

.warn-banner {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25);
  color: #B91C1C; border-radius: 12px; padding: 8px 14px;
  font-size: 12px; font-weight: 500; margin-bottom: 14px; width: auto;
}
.warn-mark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 50%;
  background: #EF4444; color: #fff; font-weight: 700; font-size: 12px; flex: 0 0 auto;
}

/* 时间轴 */
.timeline {
  display: flex; align-items: flex-start; justify-content: center;
  width: 100%; animation: fadeIn 0.4s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.tl-node { display: flex; flex-direction: column; align-items: center; padding: 0 8px; }
.tl-head { display: flex; align-items: center; gap: 6px; }
.tl-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; }
.tl-dot.blue   { background: #5B6FF5; box-shadow: 0 0 6px rgba(91, 111, 245, 0.5); }
.tl-dot.orange { background: #FFB74D; box-shadow: 0 0 6px rgba(255, 183, 77, 0.5); }
.tl-dot.cyan   { background: #4DD0E1; box-shadow: 0 0 6px rgba(77, 208, 225, 0.5); }
.tl-label { font-size: 12px; font-weight: 600; color: #4A4A6A; letter-spacing: 0.05em; white-space: nowrap; }
.tl-line {
  flex: 0 0 auto; width: 54px; height: 0;
  border-top: 2px dashed #C7CDDC; margin-top: 9px;
}
.mini-ring-wrap { position: relative; width: 45px; height: 45px; margin-top: 12px; }
.mini-ring { display: block; }
.ring-num {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.tl-value { font-size: 13px; font-weight: 600; color: #1A1A2E; margin-top: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100px; }

/* 右栏：反馈 */
.feedback-card { flex: 0 0 auto; }
.fb-btns { display: flex; gap: 8px; }
.fb-btn {
  flex: 1; height: 38px; border-radius: 10px;
  border: 1.5px solid #E0E4F0; background: #F2F4FA;
  color: #4A4A6A; font-size: 13px; font-weight: 600; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 4px;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
  white-space: nowrap;
}
.fb-btn .fb-ic { font-size: 13px; }
.fb-btn:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(91,111,245,0.14); }
.fb-btn.ok-active {
  background: linear-gradient(135deg, #34D399, #22C55E);
  border-color: #16A34A; color: #fff; box-shadow: 0 6px 16px rgba(34,197,94,0.32);
}
.fb-btn.bad-active {
  background: linear-gradient(135deg, #F87171, #EF4444);
  border-color: #DC2626; color: #fff; box-shadow: 0 6px 16px rgba(239,68,68,0.32);
}
.fb-counts { margin-top: 8px; font-size: 12px; color: #4A4A6A; text-align: center; letter-spacing: 0.02em; white-space: nowrap; }
.fb-counts b { font-weight: 700; }
.fb-counts .ok-num { color: #22C55E; }
.fb-counts .bad-num { color: #EF4444; }

/* 右栏：统计 */
.stats-card { flex: 0 0 auto; display: flex; flex-direction: column; }
.stats-row { display: flex; flex-direction: column; gap: 8px; }
.stat-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 8px; border-radius: 10px;
  background: rgba(91, 111, 245, 0.04);
  border: 1px solid rgba(91, 111, 245, 0.08);
  gap: 6px;
}
.stat-row .stat-label { flex: 0 0 auto; font-size: 11px; color: #6A6A88; white-space: nowrap; }
.stat-row .stat-value { flex: 1; min-width: 0; text-align: right; font-size: 18px; font-weight: 700; color: #5B6FF5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stat-row .stat-value .pct { font-size: 13px; }
.stat-row .stat-value.warn { color: #FFB74D; }
.stat-row .stat-value.bad { color: #EF4444; }
.subj-dots { display: flex; gap: 3px; justify-content: flex-end; flex-wrap: wrap; }
.subj-dots span {
  width: 20px; height: 20px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 700; color: #fff;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
  flex: 0 0 auto;
}
.subj-dots .dot-s { background: linear-gradient(135deg, #FF8FAE, #FF6F91); }
.subj-dots .dot-m { background: linear-gradient(135deg, #7B8FFA, #4A5CF7); }
.subj-dots .dot-e { background: linear-gradient(135deg, #FFD08C, #FFB74D); }

/* 右栏：分类历史 */
.history-card { flex: 0 0 auto; }
.history-list { display: flex; flex-direction: column; gap: 6px; }
.history-empty { text-align: center; padding: 20px 10px; color: #A0A8B8; font-size: 12px; }
.history-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 10px;
  background: rgba(91, 111, 245, 0.03);
  border: 1px solid rgba(91, 111, 245, 0.06);
  cursor: pointer; transition: background 0.2s ease, transform 0.15s ease;
}
.history-item:hover { background: rgba(91, 111, 245, 0.08); transform: translateX(2px); }
.history-dot {
  width: 24px; height: 24px; border-radius: 8px;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; color: #fff; flex: 0 0 auto;
}
.history-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.history-text {
  font-size: 12px; color: #1A1A2E; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.3;
}
.history-meta { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #6A6A88; }
.history-tag {
  display: inline-block; padding: 1px 6px; border-radius: 4px;
  font-size: 10px; font-weight: 600; color: #5B6FF5;
  background: rgba(91,111,245,0.1);
}
.history-time { color: #A0A8B8; }
.history-clear {
  margin-top: 8px; text-align: center; padding-top: 8px;
  border-top: 1px solid rgba(91,111,245,0.08);
}
.history-clear button {
  border: none; background: transparent; color: #6A6A88;
  font-size: 11px; cursor: pointer; padding: 4px 10px; border-radius: 6px;
  transition: color 0.2s ease, background 0.2s ease;
}
.history-clear button:hover { color: #EF4444; background: rgba(239,68,68,0.06); }

/* 页脚 */
.footer {
  flex: 0 0 28px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: #A0A8B8; letter-spacing: 0.02em;
  white-space: nowrap; overflow: hidden;
}

/* Toast */
#toast {
  position: fixed; left: 50%; bottom: 48px;
  transform: translateX(-50%) translateY(10px);
  background: rgba(26, 26, 46, 0.82); color: #fff;
  font-size: 13px; padding: 9px 18px; border-radius: 999px;
  opacity: 0; pointer-events: none; z-index: 99; white-space: nowrap;
  transition: opacity 0.25s ease, transform 0.25s ease;
}
#toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
"""


# ================= 新版界面交互 JS（launch head 注入，调用 Gradio REST API） =================
HEAD_JS = r"""
<script>
(function () {
  // ===== 解除 Gradio React 渲染后的宽度限制 =====
  // Gradio React 会给容器设 inline max-width，CSS !important 无法覆盖 inline style
  // 必须用 JS 主动移除，且多次执行覆盖不同渲染时机
  function fixLayout() {
    // 1. 移除所有 inline max-width
    var mw = document.querySelectorAll('[style*="max-width"]');
    for (var i = 0; i < mw.length; i++) {
      mw[i].style.removeProperty('max-width');
    }
    // 2. 强制关键容器占满全宽
    var cs = document.querySelector('.app');
    if (cs) { cs.style.width = '100%'; cs.style.maxWidth = 'none'; }
    var mn = document.querySelector('.main');
    if (mn) { mn.style.width = '100%'; mn.style.maxWidth = 'none'; }
    // 3. CSS 变量覆盖
    var root = document.documentElement;
    root.style.setProperty('--max-width', 'none', 'important');
  }
  // 多次执行：覆盖 Gradio 不同阶段的渲染
  ['load', 'DOMContentLoaded'].forEach(function(evt) {
    window.addEventListener(evt, function() {
      setTimeout(fixLayout, 100);
      setTimeout(fixLayout, 500);
      setTimeout(fixLayout, 1500);
      setTimeout(fixLayout, 3000);
    });
  });
  // 立即也执行一次
  fixLayout();

  function initApp() {
  // 9 学科快捷示例（短句）
  var EXAMPLES = {
    '语文': '下列句子中，没有语病的一项是（ ）',
    '数学': '已知函数 f(x)=x²+1，求 f(2) 的值。',
    '英语': 'He usually ______ to school by bike.',
    '物理': '一辆汽车以 20m/s 的速度匀速行驶，求刹车后 3s 内的位移。',
    '生物': '下列关于细胞中蛋白质的叙述，正确的是（ ）',
    '历史': '唐朝由盛转衰的转折点是（ ）',
    '地理': '下列关于温带季风气候的描述，正确的是（ ）',
    '政治': '下列关于价格的说法正确的是（ ）',
    '化学': '下列物质中，属于有机物的是（ ）',
  };
  var SUBJECT_ORDER = ['语文', '数学', '英语', '物理', '生物', '历史', '地理', '政治', '化学'];

  var $ = function (id) { return document.getElementById(id); };
  var inputText = $('inputText');
  var resultBody = $('resultBody');
  var fbOk = $('fbOk'), fbBad = $('fbBad');

  // ===== 历史记录存储 =====
  var HISTORY_KEY = 'edu_classify_history';
  var MAX_HISTORY = 30;
  var historyList = $('historyList');
  var historyClear = $('historyClear');

  function getHistory() {
    try {
      var data = localStorage.getItem(HISTORY_KEY);
      return data ? JSON.parse(data) : [];
    } catch (e) { return []; }
  }
  function saveHistory(list) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)); } catch (e) {}
  }
  function addHistory(text, subject, questionType, knowledgePoint, confidence) {
    var list = getHistory();
    var item = {
      text: text.length > 50 ? text.substring(0, 50) + '...' : text,
      subject: subject || '未分类',
      questionType: questionType || '',
      knowledgePoint: knowledgePoint || '',
      confidence: confidence || 0,
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    };
    list.unshift(item);
    if (list.length > MAX_HISTORY) list = list.slice(0, MAX_HISTORY);
    saveHistory(list);
    renderHistory();
  }
  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
  function getSubjectColor(subject) {
    var colors = {
      '语文': 'linear-gradient(135deg, #FF8FAE, #FF6F91)',
      '数学': 'linear-gradient(135deg, #7B8FFA, #4A5CF7)',
      '英语': 'linear-gradient(135deg, #FFD08C, #FFB74D)',
      '物理': 'linear-gradient(135deg, #6EE7B7, #10B981)',
      '化学': 'linear-gradient(135deg, #C4B5FD, #8B5CF6)',
      '生物': 'linear-gradient(135deg, #86EFAC, #22C55E)',
      '历史': 'linear-gradient(135deg, #FCA5A5, #EF4444)',
      '地理': 'linear-gradient(135deg, #93C5FD, #3B82F6)',
      '政治': 'linear-gradient(135deg, #FCD34D, #F59E0B)'
    };
    return colors[subject] || 'linear-gradient(135deg, #94A3B8, #64748B)';
  }
  function renderHistory() {
    var list = getHistory();
    if (!list.length) {
      historyList.innerHTML = '<div class="history-empty">暂无分类记录，开始分类后这里会显示历史</div>';
      historyClear.style.display = 'none';
      return;
    }
    historyClear.style.display = 'block';
    var html = '';
    list.forEach(function (item, idx) {
      var dotColor = getSubjectColor(item.subject);
      var mainTag = item.subject;
      var subTag = item.questionType ? ' · ' + item.questionType : '';
      html += '<div class="history-item" data-idx="' + idx + '">'
        + '<span class="history-dot" style="background:' + dotColor + '">' + mainTag.charAt(0) + '</span>'
        + '<div class="history-info">'
        + '<div class="history-text">' + escapeHtml(item.text) + '</div>'
        + '<div class="history-meta">'
        + '<span class="history-tag">' + mainTag + subTag + '</span>'
        + '<span class="history-time">' + item.time + '</span>'
        + '</div>'
        + '</div></div>';
    });
    historyList.innerHTML = html;
    historyList.querySelectorAll('.history-item').forEach(function (el) {
      el.addEventListener('click', function () {
        var idx = parseInt(el.dataset.idx, 10);
        var item = getHistory()[idx];
        if (item) {
          inputText.value = item.text;
          inputText.focus();
          toast('已恢复历史题目');
        }
      });
    });
  }
  $('btnClearHistory').addEventListener('click', function () {
    if (confirm('确定要清空所有分类历史吗？')) {
      saveHistory([]);
      renderHistory();
      toast('历史已清空');
    }
  });
  renderHistory();

  // 轻量 Toast
  var toastTimer = null;
  function toast(msg) {
    var t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove('show'); }, 2000);
  }

  // 调用 Gradio REST API（同步返回 data）
  async function callApi(name, data) {
    var res = await fetch('gradio_api/api/' + name, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: data, session_hash: 'static-demo' }),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  }

  // 迷你圆环动画：描边过渡 + 数字从 0 递增
  function animateRings(root) {
    root.querySelectorAll('.ring-fg').forEach(function (c) {
      if (c.dataset.animated === '1') return;
      var off = parseFloat(c.getAttribute('data-offset'));
      if (isNaN(off)) return;
      c.dataset.animated = '1';
      requestAnimationFrame(function () { c.style.strokeDashoffset = off; });
    });
    root.querySelectorAll('.ring-num').forEach(function (t) {
      if (t.dataset.animated === '1') return;
      var target = parseInt(t.getAttribute('data-target'), 10) || 0;
      var color = t.getAttribute('data-color') || '#5B6FF5';
      t.dataset.animated = '1';
      var start = null, dur = 650;
      function step(ts) {
        if (!start) start = ts;
        var p = Math.min(1, (ts - start) / dur);
        var e = 1 - Math.pow(1 - p, 3);
        t.textContent = Math.round(target * e) + '%';
        t.style.color = color;
        if (p < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  // 生成 9 个快捷示例按钮
  var grid = $('exampleGrid');
  SUBJECT_ORDER.forEach(function (s) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = s;
    btn.addEventListener('click', function () {
      inputText.value = EXAMPLES[s];
      inputText.focus();
    });
    grid.appendChild(btn);
  });

  // 加载态
  function renderLoading() {
    resultBody.innerHTML = '<div class="result-loading">'
      + '<span class="ldot"></span><span class="ldot"></span><span class="ldot"></span>'
      + '<span style="margin-left:6px">正在分类...</span></div>';
  }
  function renderEmpty() {
    resultBody.innerHTML = '<div class="result-empty">⏳ 等待分类...</div>';
  }
  function resetFeedback() {
    fbOk.classList.remove('ok-active');
    fbBad.classList.remove('bad-active');
  }

  // 开始分类 → 调后端真实预测
  $('btnClassify').addEventListener('click', function () {
    var text = inputText.value.trim();
    if (!text) { toast('请先输入题目文本'); inputText.focus(); return; }
    renderLoading();
    resetFeedback();
    callApi('classify', [text]).then(function (r) {
      var html = r.data[0];
      var count = r.data[1];
      var avg = r.data[2];
      resultBody.innerHTML = html;
      animateRings(resultBody);
      $('statCount').textContent = count;
      $('statConf').textContent = count ? avg : '--';

      // 保存到历史记录
      var vals = Array.from(resultBody.querySelectorAll('.tl-value')).map(function (e) { return e.textContent.trim(); });
      var rings = Array.from(resultBody.querySelectorAll('.ring-num'));
      var subject = vals[0] || '未分类';
      var questionType = vals[1] || '';
      var knowledgePoint = vals[2] || '';
      var confidence = rings.length >= 1 ? (parseInt(rings[0].getAttribute('data-target') || '0', 10) || 0) : 0;
      addHistory(text, subject, questionType, knowledgePoint, confidence);

      postClassify(text);   // 平台桥接：把本次真实分类结果发往父页面 platform.html
    }).catch(function (e) {
      resultBody.innerHTML = '<div class="result-empty result-error">⚠️ 分类失败：' + e.message + '</div>';
    });
  });

  // 清空（纯前端）
  $('btnClear').addEventListener('click', function () {
    inputText.value = '';
    renderEmpty();
    resetFeedback();
  });

  // 反馈：高亮 + 计数 + 后端持久化
  function doFeedback(active, other, apiName) {
    active.classList.add(active === fbOk ? 'ok-active' : 'bad-active');
    other.classList.remove('ok-active', 'bad-active');
    var text = inputText.value.trim();
    callApi(apiName, [text]).then(function (r) {
      $('countOk').textContent = r.data[0];
      $('countBad').textContent = r.data[1];
      notifyFeedback();   // 平台桥接：反馈计数同步给父页面（可选增强）
    }).catch(function () {
      // 后端持久化失败时本地兜底 +1
      if (apiName === 'feedback_ok') $('countOk').textContent = (parseInt($('countOk').textContent, 10) || 0) + 1;
      else $('countBad').textContent = (parseInt($('countBad').textContent, 10) || 0) + 1;
      notifyFeedback();   // 平台桥接：本地兜底后同样同步计数
    });
  }
  fbOk.addEventListener('click', function () { doFeedback(fbOk, fbBad, 'feedback_ok'); });
  fbBad.addEventListener('click', function () { doFeedback(fbBad, fbOk, 'feedback_bad'); });

  // 历史记录按钮 → 滚动到历史区域
  $('btnHistory').addEventListener('click', function () {
    var historyCard = document.querySelector('.history-card');
    if (historyCard) {
      historyCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
      historyCard.style.transition = 'box-shadow 0.3s ease';
      historyCard.style.boxShadow = '0 0 0 3px rgba(91,111,245,0.4), 0 8px 32px rgba(0,0,0,0.08)';
      setTimeout(function () { historyCard.style.boxShadow = ''; }, 1500);
    }
  });

  // ===== 平台桥接：与 platform.html 的 postMessage 通道（仅 UI 层，不改任何业务逻辑）=====
  // 分类成功后：从结果 DOM 提取结构化三级标签 + 置信度，发给父页面
  function postClassify(text) {
    try {
      var vals = Array.from(resultBody.querySelectorAll('.tl-value')).map(function (e) { return e.textContent.trim(); });
      var rings = Array.from(resultBody.querySelectorAll('.ring-num'));
      if (vals.length < 3 || rings.length < 3) return;
      var keys = ['subject', 'question_type', 'knowledge_point'];
      var result = {}, confidence = {};
      for (var i = 0; i < 3; i++) {
        result[keys[i]] = vals[i];
        confidence[keys[i]] = Math.min(100, parseInt(rings[i].getAttribute('data-target') || '0', 10)) / 100;
      }
      window.parent && window.parent.postMessage({
        type: 'edu-classify', result: result, confidence: confidence, text: text
      }, '*');
    } catch (e) {}
  }
  // 反馈计数同步（可选增强）：把 ok/bad 计数同步给父页面
  function notifyFeedback() {
    try {
      window.parent && window.parent.postMessage({
        type: 'edu-feedback',
        ok: parseInt($('countOk').textContent, 10) || 0,
        bad: parseInt($('countBad').textContent, 10) || 0
      }, '*');
    } catch (e) {}
  }
  }
  // head 注入的脚本执行时机早于 Gradio 的 React 异步渲染，
  // 需等待必需元素挂载后再初始化（轮询 + MutationObserver 双保险）
  var _booted = false;
  function boot() {
    if (_booted) return;
    if (document.getElementById('exampleGrid') && document.getElementById('btnClassify') && document.getElementById('resultBody')) {
      _booted = true;
      initApp();
    }
  }
  boot();
  try {
    var _obs = new MutationObserver(boot);
    _obs.observe(document.documentElement, { childList: true, subtree: true });
  } catch (_e) {}
  (function tick() { boot(); if (!_booted) setTimeout(tick, 200); })();

  // 心跳独立于界面渲染：iframe 脚本一加载即每 5s 发送 edu-ready（服务存活检测）。
  // 原实现嵌套在 initApp 内，若 Gradio 界面渲染异常导致 initApp 不执行，心跳将永不发出，
  // 父页面会误判"后端未启动"。心跳仅依赖 window.parent，可安全独立运行。
  (function heartbeat() {
    try { window.parent && window.parent.postMessage({ type: 'edu-ready' }, '*'); } catch (e) {}
    setTimeout(heartbeat, 5000);
  })();
})();
</script>
"""


# ================= Gradio Blocks =================
def build_demo():
    with gr.Blocks(title="教育题目分层分类系统") as demo:
        # 新版界面（纯 HTML）+ 全局样式
        gr.HTML("<style>" + UI_CSS + "</style>")
        gr.HTML(render_ui_html())

        # 隐藏的 schema 组件（仅用于注册 REST API 的 inputs/outputs，不参与界面）
        textbox = gr.Textbox(visible=False)
        out_result = gr.HTML(visible=False)
        out_count = gr.Number(visible=False)
        out_avg = gr.Number(visible=False)
        out_ok = gr.Number(visible=False)
        out_bad = gr.Number(visible=False)
        go_btn = gr.Button("__trigger__", visible=False)
        ok_btn = gr.Button("__ok__", visible=False)
        bad_btn = gr.Button("__bad__", visible=False)

        # REST API 注册（前端 JS 通过 fetch /gradio_api/api/... 调用）
        go_btn.click(
            classify,
            inputs=[textbox],
            outputs=[out_result, out_count, out_avg],
            api_name="classify",
        )
        ok_btn.click(
            lambda t: submit_feedback(t, True),
            inputs=[textbox],
            outputs=[out_ok, out_bad],
            api_name="feedback_ok",
        )
        bad_btn.click(
            lambda t: submit_feedback(t, False),
            inputs=[textbox],
            outputs=[out_ok, out_bad],
            api_name="feedback_bad",
        )

    return demo


def build_platform_app():
    """FastAPI 主应用：根路径 / 返回"智慧教研平台"后台页(platform.html)，
    原 Gradio 分类器挂载到 /classifier，供平台页 iframe 嵌入。
    /api/* 下提供 RESTful 接口，支撑前端各视图的真实数据流。"""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from typing import List, Optional

    demo = build_demo()
    # head/css/theme 在构造 config 时为空，赋值后调用 get_config_file() 重新生成
    # 完整 config（含 body_css），前端页面据此注入桥接 JS 与样式
    demo.head = HEAD_JS
    demo.css = UI_CSS
    demo.theme = gr.themes.Default()
    demo.css_paths = None
    # 手动构建 theme 样式变量：FastAPI 挂载绕过了 launch()，theme_css 需自行生成。
    # 否则 /theme.css 路由抛 AttributeError → 500，Gradio 前端初始化异常，
    # iframe 内分类器无法正常显示（此前"后端未启动"引导层的真正根源之一）。
    demo._set_html_css_theme_variables()
    demo.config = demo.get_config_file()
    demo.config["root_path"] = "/classifier"

    gradio_app = demo.app
    gradio_app.root_path = "/classifier"

    platform_path = os.path.join(ROOT, "platform.html")

    api = FastAPI(title="智慧教研平台")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ================= 内存级数据存储 =================
    # 全局内存存储（服务重启后清空）
    question_db = []          # 已存入题库的题目列表
    feedback_log = []         # 反馈记录
    classification_history = []  # 最近 50 条分类历史
    stats = {
        "total_processed": 0,      # 累计分类数
        "total_correct": 0,        # 累计正确反馈
        "total_wrong": 0,          # 累计错误反馈
        "avg_confidence": 0.0,     # 平均置信度
    }
    _seq = {"q": 0, "h": 0}  # 自增 ID 计数器

    def _now() -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ================= 种子题目（服务启动时自动注入）=================
    _seed_questions = [
        # 语文
        {"text": "下列词语中，加点字的读音全都正确的一组是（ ）A. 绮丽(yǐ) B. 亘古(gèn) C. 庇护(pì) D. 脂肪(zhǐ)", "subject": "语文", "q_type": "选择题", "knowledge": "语文::基础知识"},
        {"text": "《红楼梦》中“机关算尽太聪明，反误了卿卿性命”说的是王熙凤。（ ）", "subject": "语文", "q_type": "判断题", "knowledge": "语文::阅读与鉴赏"},
        {"text": "请默写李白《静夜思》全诗，并简要分析作者的思乡之情。", "subject": "语文", "q_type": "解答题", "knowledge": "语文::古代汉语知识"},
        {"text": "“落霞与孤鹜齐飞，秋水共长天一色”出自王勃的哪部作品？请说明该骈文的艺术特色。", "subject": "语文", "q_type": "解答题", "knowledge": "语文::阅读与鉴赏"},
        {"text": "鲁迅《狂人日记》是中国第一部现代白话小说。（ ）", "subject": "语文", "q_type": "判断题", "knowledge": "语文::阅读与鉴赏"},
        # 数学
        {"text": "已知函数 f(x)=x²-4x+3，则 f(x) 的最小值为（ ）A. -1 B. 0 C. 1 D. 3", "subject": "数学", "q_type": "选择题", "knowledge": "数学::代数"},
        {"text": "若 sinα=3/5，且 α 为第二象限角，则 cosα 的值为 -4/5。（ ）", "subject": "数学", "q_type": "判断题", "knowledge": "数学::三角函数"},
        {"text": "求数列 2,5,8,11,… 的第 20 项及前 20 项的和。", "subject": "数学", "q_type": "解答题", "knowledge": "数学::数列"},
        {"text": "已知正方体 ABCD-A₁B₁C₁D₁ 的棱长为 2，求异面直线 AB₁ 与 CD₁ 的距离。", "subject": "数学", "q_type": "解答题", "knowledge": "数学::立体几何"},
        {"text": "从 5 名男生和 3 名女生中任选 3 人参加比赛，至少有 1 名女生的概率是 23/28。（ ）", "subject": "数学", "q_type": "判断题", "knowledge": "数学::排列组合与概率统计"},
        # 英语
        {"text": "He usually ______ to school by bike. A. go B. goes C. going D. went", "subject": "英语", "q_type": "选择题", "knowledge": "英语::词法"},
        {"text": "“I have been living here since 2010.” 这句话使用了现在完成进行时。（ ）", "subject": "英语", "q_type": "判断题", "knowledge": "英语::句法"},
        {"text": "请用英语写一篇 80-100 词的短文，描述你最喜欢的季节并说明理由。", "subject": "英语", "q_type": "解答题", "knowledge": "英语::写作"},
        {"text": "The book ______ (write) by Hemingway is very popular. 用括号中动词的适当形式填空并解释语法原因。", "subject": "英语", "q_type": "解答题", "knowledge": "英语::句法"},
        {"text": "“We will have finished the project by next Friday.” 使用了将来完成时。（ ）", "subject": "英语", "q_type": "判断题", "knowledge": "英语::时态"},
        # 物理
        {"text": "一个物体做自由落体运动，下落 2s 时的速度为（g取10m/s²）（ ）A. 10m/s B. 20m/s C. 30m/s D. 40m/s", "subject": "物理", "q_type": "选择题", "knowledge": "物理::力学"},
        {"text": "光在真空中的传播速度约为 3×10⁸ m/s。（ ）", "subject": "物理", "q_type": "判断题", "knowledge": "物理::电磁学"},
        {"text": "质量为 2kg 的物体在水平面上受到 10N 的水平推力作用，加速度为 4m/s²，求物体受到的摩擦力大小。", "subject": "物理", "q_type": "解答题", "knowledge": "物理::力学"},
        {"text": "请设计一个实验验证楞次定律，画出实验装置图并说明操作步骤。", "subject": "物理", "q_type": "解答题", "knowledge": "物理::电磁学"},
        {"text": "布朗运动是分子热运动的直接表现。（ ）", "subject": "物理", "q_type": "判断题", "knowledge": "物理::热学"},
        # 化学
        {"text": "下列物质中，属于有机物的是（ ）A. 二氧化碳 B. 乙醇 C. 氯化钠 D. 硫酸", "subject": "化学", "q_type": "选择题", "knowledge": "化学::常见有机物及其应用"},
        {"text": "在 25℃ 时，纯水的 pH 值等于 7。（ ）", "subject": "化学", "q_type": "判断题", "knowledge": "化学::化学基本概念和基本理论"},
        {"text": "0.1mol/L 的盐酸溶液 100mL 与 0.2mol/L 的 NaOH 溶液 50mL 混合后，溶液的 pH 是多少？", "subject": "化学", "q_type": "解答题", "knowledge": "化学::化学反应原理"},
        {"text": "实验室如何用乙醇和乙酸制取乙酸乙酯？请写出化学方程式并说明实验注意事项。", "subject": "化学", "q_type": "解答题", "knowledge": "化学::化学实验"},
        {"text": "催化剂能改变化学反应速率但不改变平衡常数。（ ）", "subject": "化学", "q_type": "判断题", "knowledge": "化学::化学平衡"},
        # 生物
        {"text": "细胞的基本结构中，具有双层膜的细胞器是（ ）A. 核糖体 B. 线粒体 C. 高尔基体 D. 溶酶体", "subject": "生物", "q_type": "选择题", "knowledge": "生物::分子与细胞"},
        {"text": "DNA 分子的基本组成单位是脱氧核糖核苷酸。（ ）", "subject": "生物", "q_type": "判断题", "knowledge": "生物::遗传与进化"},
        {"text": "试述光合作用的光反应和暗反应的过程及两者之间的关系。", "subject": "生物", "q_type": "解答题", "knowledge": "生物::分子与细胞"},
        {"text": "简述甲状腺激素分泌的分级调节和反馈调节机制。", "subject": "生物", "q_type": "解答题", "knowledge": "生物::稳态与环境"},
        {"text": "减数分裂过程中，同源染色体分离发生在减数第一次分裂后期。（ ）", "subject": "生物", "q_type": "判断题", "knowledge": "生物::遗传与进化"},
        # 历史
        {"text": "中国古代科举制度正式创立于（ ）A. 汉朝 B. 隋朝 C. 唐朝 D. 宋朝", "subject": "历史", "q_type": "选择题", "knowledge": "历史::选考一 历史上的重大改革"},
        {"text": "辛亥革命推翻了统治中国两千多年的封建君主专制制度。（ ）", "subject": "历史", "q_type": "判断题", "knowledge": "历史::近代中国"},
        {"text": "结合史实分析“闭关锁国”政策对明清时期中国社会发展的影响。", "subject": "历史", "q_type": "解答题", "knowledge": "历史::古代世界"},
        {"text": "简述文艺复兴运动兴起的历史背景、核心思想及代表人物。", "subject": "历史", "q_type": "解答题", "knowledge": "历史::近代世界"},
        {"text": "第一次世界大战的导火线是萨拉热窝事件。（ ）", "subject": "历史", "q_type": "判断题", "knowledge": "历史::选考三 20 世纪的战争与和平"},
        # 地理
        {"text": "下列关于温带季风气候的描述，正确的是（ ）A. 终年高温多雨 B. 夏季高温多雨，冬季寒冷干燥 C. 终年温和湿润 D. 夏季炎热干燥，冬季温和多雨", "subject": "地理", "q_type": "选择题", "knowledge": "地理::自然地理"},
        {"text": "长江是我国第一长河，全长约 6300 千米。（ ）", "subject": "地理", "q_type": "判断题", "knowledge": "地理::自然地理"},
        {"text": "试述我国西北地区荒漠化严重的自然原因和人为原因，并提出治理措施。", "subject": "地理", "q_type": "解答题", "knowledge": "地理::环境保护"},
        {"text": "分析长三角地区成为我国经济最发达地区的区位条件。", "subject": "地理", "q_type": "解答题", "knowledge": "地理::区域发展"},
        {"text": "印度是世界上人口最多的国家。（ ）", "subject": "地理", "q_type": "判断题", "knowledge": "地理::人文地理"},
        # 政治
        {"text": "下列关于价格的说法正确的是（ ）A. 价格由价值决定 B. 价格由供求决定 C. 价格与价值完全无关 D. 价格只受政府管控", "subject": "政治", "q_type": "选择题", "knowledge": "政治::经济生活"},
        {"text": "我国的根本政治制度是人民代表大会制度。（ ）", "subject": "政治", "q_type": "判断题", "knowledge": "政治::政治生活"},
        {"text": "运用经济生活知识，分析市场调节的优势和局限性，并说明为什么需要科学的宏观调控。", "subject": "政治", "q_type": "解答题", "knowledge": "政治::经济生活"},
        {"text": "运用对立统一规律，谈谈你对“互联网是一把双刃剑”的理解。", "subject": "政治", "q_type": "解答题", "knowledge": "政治::其他"},
        {"text": "文化创新的源泉和动力是社会实践。（ ）", "subject": "政治", "q_type": "判断题", "knowledge": "政治::文化生活"},
    ]

    # 注入种子题目
    for sq in _seed_questions:
        _seq["q"] += 1
        question_db.append({
            "id": _seq["q"],
            "text": sq["text"],
            "subject": sq["subject"],
            "q_type": sq["q_type"],
            "knowledge": sq["knowledge"],
            "created_at": _now(),
        })

    # ================= Pydantic 请求模型 =================
    class ClassifyReq(BaseModel):
        text: str

    class FeedbackReq(BaseModel):
        text: str = ""
        subject: str = ""
        correct: bool = True

    class SaveQuestionReq(BaseModel):
        text: str
        subject: str
        q_type: str
        knowledge: str

    class GeneratePaperReq(BaseModel):
        subjects: List[str] = []
        types: List[str] = []
        count: int = 5

    # ================= RESTful API 端点 =================
    @api.get("/", response_class=HTMLResponse)
    def platform_index():
        if os.path.exists(platform_path):
            with open(platform_path, encoding="utf-8") as f:
                return f.read()
        return ("<html><body style='font-family:sans-serif;padding:40px'>"
                "<h3>未找到 platform.html</h3>"
                "<p>请确认文件位于项目根目录：D:\\edu_hierarchical_classifier\\platform.html</p></body></html>")

    @api.get("/health")
    def health_check():
        return {"status": "ok", "service": "education-classifier-platform", "classifier": True}

    # 1. POST /api/classify —— 分类接口（调用真实模型）
    @api.post("/api/classify")
    async def api_classify(req: ClassifyReq):
        text = (req.text or "").strip()
        if not text:
            return JSONResponse({"error": "text is required"}, status_code=400)
        try:
            result = get_predictor().predict(text)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        conf = result["confidence"]
        # 更新统计
        avg_conf = (conf["subject"] + conf["question_type"] + conf["knowledge_point"]) / 3
        stats["total_processed"] += 1
        # 滑动平均置信度
        prev = stats["avg_confidence"]
        n = stats["total_processed"]
        stats["avg_confidence"] = round((prev * (n - 1) + avg_conf) / n, 4)
        # 写入分类历史
        _seq["h"] += 1
        hist_item = {
            "id": _seq["h"],
            "text": text[:200],
            "subject": result["subject"],
            "q_type": result["question_type"],
            "knowledge": result["knowledge_point"],
            "confidences": conf,
            "timestamp": _now(),
        }
        classification_history.insert(0, hist_item)
        if len(classification_history) > 50:
            classification_history[:] = classification_history[:50]
        return {
            "subject": result["subject"],
            "q_type": result["question_type"],
            "knowledge": result["knowledge_point"],
            "confidences": conf,
        }

    # 2. POST /api/feedback —— 反馈接口
    @api.post("/api/feedback")
    async def api_feedback(req: FeedbackReq):
        if req.correct:
            stats["total_correct"] += 1
        else:
            stats["total_wrong"] += 1
        feedback_log.append({
            "question_text": req.text[:200],
            "subject": req.subject,
            "correct_flag": bool(req.correct),
            "timestamp": _now(),
        })
        return {
            "status": "ok",
            "total_correct": stats["total_correct"],
            "total_wrong": stats["total_wrong"],
        }

    # 3. POST /api/save_question —— 存入题库
    @api.post("/api/save_question")
    async def api_save_question(req: SaveQuestionReq):
        _seq["q"] += 1
        item = {
            "id": _seq["q"],
            "text": req.text,
            "subject": req.subject,
            "q_type": req.q_type,
            "knowledge": req.knowledge,
            "created_at": _now(),
        }
        question_db.insert(0, item)
        return {"status": "ok", "id": _seq["q"]}

    # 4. DELETE /api/questions/{qid} —— 删除题目
    @api.delete("/api/questions/{qid}")
    async def api_delete_question(qid: int):
        before = len(question_db)
        question_db[:] = [q for q in question_db if q["id"] != qid]
        deleted = before - len(question_db)
        if deleted == 0:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"status": "ok"}

    # 5. GET /api/questions —— 题目列表（支持筛选）
    @api.get("/api/questions")
    async def api_questions(subject: str = "", q_type: str = "", keyword: str = ""):
        items = question_db[:]
        if subject:
            items = [x for x in items if x["subject"] == subject]
        if q_type:
            items = [x for x in items if x["q_type"] == q_type]
        if keyword:
            kw = keyword.lower()
            items = [x for x in items if kw in x["text"].lower() or kw in x["knowledge"].lower()]
        return {"total": len(items), "items": items}

    # 6. GET /api/stats —— 聚合统计
    @api.get("/api/stats")
    async def api_stats():
        # 学科分布（基于分类历史）
        subj_dist = {}
        for h in classification_history:
            s = h["subject"]
            subj_dist[s] = subj_dist.get(s, 0) + 1
        return {
            "total_processed": stats["total_processed"],
            "total_correct": stats["total_correct"],
            "total_wrong": stats["total_wrong"],
            "avg_confidence": stats["avg_confidence"],
            "question_count": len(question_db),
            "subject_distribution": subj_dist,
        }

    # 7. GET /api/history —— 分类历史
    @api.get("/api/history")
    async def api_history():
        return classification_history[:20]

    # 8. POST /api/generate_paper —— 智能组卷
    @api.post("/api/generate_paper")
    async def api_generate_paper(req: GeneratePaperReq):
        pool = question_db[:]
        if req.subjects:
            pool = [x for x in pool if x["subject"] in req.subjects]
        if req.types:
            pool = [x for x in pool if x["q_type"] in req.types]
        questions = pool[:req.count]
        return {"questions": questions, "total_matched": len(pool)}

    # 9. GET /api/analysis —— 学情分析
    @api.get("/api/analysis")
    async def api_analysis():
        # 基于 feedback_log 计算各学科正确率
        subj_stats = {}  # subject -> {correct, total}
        for fb in feedback_log:
            s = fb["subject"]
            if not s:
                continue
            if s not in subj_stats:
                subj_stats[s] = {"correct": 0, "total": 0}
            subj_stats[s]["total"] += 1
            if fb["correct_flag"]:
                subj_stats[s]["correct"] += 1
        subject_correct_rates = {}
        for s, v in subj_stats.items():
            subject_correct_rates[s] = round(v["correct"] / v["total"], 4) if v["total"] else 0
        # 薄弱知识点：统计错误反馈中各知识点出现次数
        weak = {}
        for fb in feedback_log:
            if not fb["correct_flag"] and fb["subject"]:
                weak[fb["subject"]] = weak.get(fb["subject"], 0) + 1
        return {
            "weak_points": weak,
            "subject_correct_rates": subject_correct_rates,
            "feedback_total": len(feedback_log),
        }

    api.mount("/classifier", gradio_app)
    return api


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_platform_app(), host="127.0.0.1", port=7860)
