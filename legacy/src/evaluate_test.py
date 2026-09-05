# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 测试集端到端验收评估
功能：
  1. 加载训练好的预测器（微调主干 + 三头 + 动态量化）
  2. 在测试集上批量预测，计算三级分类指标
  3. 输出 logs/test_evaluation.json（含级联准确率、单条推理耗时）

运行：venv\Scripts\python src\evaluate_test.py
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, write_log

ROOT = get_project_root()
setup_environment()

import pandas as pd
from sklearn.metrics import accuracy_score, f1_score

from predict import load_predictor


def main():
    write_log("evaluate_test", "开始测试集验收评估...")
    test_path = os.path.join(ROOT, "data", "processed", "test.csv")
    df = pd.read_csv(test_path)
    write_log("evaluate_test", f"测试样本：{len(df)} 条")

    predictor = load_predictor()

    # 批量预测
    results = []
    t0 = time.time()
    for _, row in df.iterrows():
        text = str(row["text"])
        try:
            res = predictor.predict(text)
            results.append({
                "subject": res["subject"],
                "question_type": res["question_type"],
                "knowledge_point": res["knowledge_point"],
                "true_subject": str(row["subject"]),
                "true_type": str(row["question_type"]),
                "true_knowledge": str(row["knowledge_point"]),
            })
        except Exception as e:
            write_log("evaluate_test", f"预测失败：{e}", "WARN")
    total_time = time.time() - t0

    # 指标计算
    n = len(results)
    subj_acc = accuracy_score([r["true_subject"] for r in results], [r["subject"] for r in results])
    subj_f1 = f1_score([r["true_subject"] for r in results], [r["subject"] for r in results], average="macro", zero_division=0)
    type_f1 = f1_score([r["true_type"] for r in results], [r["question_type"] for r in results], average="macro", zero_division=0)
    type_acc = accuracy_score([r["true_type"] for r in results], [r["question_type"] for r in results])
    kp_f1 = f1_score([r["true_knowledge"] for r in results], [r["knowledge_point"] for r in results], average="macro", zero_division=0)
    kp_acc = accuracy_score([r["true_knowledge"] for r in results], [r["knowledge_point"] for r in results])
    # 级联准确率：三级标签全部正确
    cascade = sum(1 for r in results if r["subject"] == r["true_subject"]
                  and r["question_type"] == r["true_type"]
                  and r["knowledge_point"] == r["true_knowledge"]) / n

    report = {
        "n_test": n,
        "subject": {"accuracy": round(float(subj_acc), 4), "f1_macro": round(float(subj_f1), 4)},
        "question_type": {"accuracy": round(float(type_acc), 4), "f1_macro": round(float(type_f1), 4)},
        "knowledge_point": {"accuracy": round(float(kp_acc), 4), "f1_macro": round(float(kp_f1), 4)},
        "cascade_accuracy": round(float(cascade), 4),
        "avg_inference_ms": round(total_time / n * 1000, 1) if n else 0,
        "quantized": predictor.quantized,
        "device": str(predictor.device),
    }

    write_log("evaluate_test", "=" * 50)
    write_log("evaluate_test", f"学科   acc={subj_acc:.4f}")
    write_log("evaluate_test", f"题型   acc={type_acc:.4f} f1={type_f1:.4f}")
    write_log("evaluate_test", f"知识点 acc={kp_acc:.4f} f1={kp_f1:.4f}")
    write_log("evaluate_test", f"级联准确率={cascade:.4f}，平均单条耗时={report['avg_inference_ms']}ms")

    out_path = os.path.join(ROOT, "logs", "test_evaluation.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    write_log("evaluate_test", f"报告已保存：{out_path}")


if __name__ == "__main__":
    main()
