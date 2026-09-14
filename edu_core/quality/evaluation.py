"""golden set 回归评估。

对齐 knowforge 的评测回归思路：用固定冻结的 golden 测试集（eval_sets/golden_test_set.json，
从 data/processed/test.csv 抽样固化）对指定模型版本做端到端评估，产出 JSON 报告，
供质量门禁（gate.py）判定，指标不达标则阻断激活。

指标口径与训练验证一致：
- subject_acc：学科准确率
- type_f1：题型宏平均 F1
- knowledge_f1：知识点宏平均 F1
- cascade_acc：三级全对（级联）准确率
- avg_latency_ms：平均单条推理耗时
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from sklearn.metrics import accuracy_score, f1_score

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)


def load_golden_set(settings: Settings) -> list[dict]:
    """加载 golden 测试集（每条：text/subject/question_type/knowledge_point）。"""
    path = settings.abs_path(settings.eval_sets_dir) / "golden_test_set.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"golden 测试集不存在：{path}，请先运行 scripts/export_golden_set.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("cases"):
        raise ValueError(f"golden 测试集为空：{path}")
    return data["cases"]


def load_evaluation_set(settings: Settings, dataset: str = "golden_test_set") -> list[dict]:
    """加载受支持的冻结评测集，不允许任意路径绕过项目目录。"""
    if dataset not in {"golden_test_set", "bench_clean"}:
        raise ValueError("评测集仅支持 golden_test_set 或 bench_clean")
    if dataset == "golden_test_set":
        return load_golden_set(settings)
    path = settings.abs_path(settings.eval_sets_dir) / "bench_clean.json"
    if not path.is_file():
        raise FileNotFoundError(f"干净基准不存在：{path}，请先运行 scripts/build_clean_bench.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("dataset") != "bench_clean" or not data.get("cases"):
        raise ValueError(f"干净基准格式无效：{path}")
    return data["cases"]


def evaluate_predictor(predict_fn: Callable[[str], dict], cases: list[dict],
                       version: str, dataset_name: str = "golden_test_set") -> dict:
    """对预测函数跑完 golden set 并计算指标。

    Args:
        predict_fn: 输入题目文本，返回含 subject/question_type/knowledge_point
                    与 confidence 的预测 dict（predictor.predict）。
        cases: golden set 用例列表（含三级真值标签）。
        version: 被评估的模型版本号。
    Returns:
        评估报告 dict（metrics + 明细摘要），可整体写入 reports/evaluation/*.json。
    """
    y_true: dict[str, list[str]] = {"subject": [], "question_type": [], "knowledge_point": []}
    y_pred: dict[str, list[str]] = {k: [] for k in y_true}
    cascade_correct = 0
    latencies: list[float] = []

    started_total = time.perf_counter()
    for case in cases:
        result = predict_fn(case["text"])
        y_true["subject"].append(case["subject"])
        y_pred["subject"].append(result["subject"])
        y_true["question_type"].append(case["question_type"])
        y_pred["question_type"].append(result["question_type"])
        y_true["knowledge_point"].append(case["knowledge_point"])
        y_pred["knowledge_point"].append(result["knowledge_point"])
        if (result["subject"] == case["subject"]
                and result["question_type"] == case["question_type"]
                and result["knowledge_point"] == case["knowledge_point"]):
            cascade_correct += 1
        latencies.append(float(result.get("latency_ms") or 0.0))
    elapsed_total = round((time.perf_counter() - started_total) * 1000, 2)

    metrics = {
        "n_samples": len(cases),
        "subject_acc": round(float(accuracy_score(y_true["subject"], y_pred["subject"])), 4),
        "subject_f1": round(float(f1_score(y_true["subject"], y_pred["subject"],
                                           average="macro", zero_division=0)), 4),
        "type_acc": round(float(accuracy_score(y_true["question_type"], y_pred["question_type"])), 4),
        "type_f1": round(float(f1_score(y_true["question_type"], y_pred["question_type"],
                                        average="macro", zero_division=0)), 4),
        "knowledge_acc": round(float(accuracy_score(y_true["knowledge_point"], y_pred["knowledge_point"])), 4),
        "knowledge_f1": round(float(f1_score(y_true["knowledge_point"], y_pred["knowledge_point"],
                                             average="macro", zero_division=0)), 4),
        "cascade_acc": round(cascade_correct / len(cases), 4) if cases else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "total_eval_ms": elapsed_total,
    }

    # 错误样本摘要（用于 Bad Case 分析，最多保留 20 条）
    errors = []
    for i, case in enumerate(cases):
        if (y_pred["subject"][i] != case["subject"]
                or y_pred["question_type"][i] != case["question_type"]
                or y_pred["knowledge_point"][i] != case["knowledge_point"]):
            errors.append({
                "text_preview": case["text"][:120],
                "expected": {"subject": case["subject"], "question_type": case["question_type"],
                             "knowledge_point": case["knowledge_point"]},
                "predicted": {"subject": y_pred["subject"][i], "question_type": y_pred["question_type"][i],
                              "knowledge_point": y_pred["knowledge_point"][i]},
            })
        if len(errors) >= 20:
            break

    # 分学科准确率（薄弱学科定位）
    by_subject: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    for i, case in enumerate(cases):
        by_subject[case["subject"]]["total"] += 1
        if (y_pred["subject"][i] == case["subject"]
                and y_pred["question_type"][i] == case["question_type"]
                and y_pred["knowledge_point"][i] == case["knowledge_point"]):
            by_subject[case["subject"]]["correct"] += 1
    subject_cascade = {
        s: round(v["correct"] / v["total"], 4) for s, v in sorted(by_subject.items())
    }

    return {
        "version": version,
        "dataset": dataset_name,
        "evaluated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": metrics,
        "subject_cascade_accuracy": subject_cascade,
        "error_samples": errors,
    }


def save_report(report: dict, settings: Settings, name: str | None = None) -> Path:
    """评估报告落盘 reports/evaluation/<name>.json。

    name 可传：
      - None：默认 <version>_evaluation.json；
      - 纯文件名：落在 reports/evaluation/ 下；
      - 含目录分隔符的相对路径：按项目根解析（如 reports/evaluation/smoke.json）。
    """
    if name and ("/" in name or "\\" in name):
        path = settings.abs_path(name)
    else:
        out_dir = settings.abs_path(settings.reports_dir) / "evaluation"
        path = out_dir / (name or f"{report['version']}_evaluation.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("评估报告已写入 %s", path)
    return path
