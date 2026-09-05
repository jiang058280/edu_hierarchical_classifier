# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 模型评估脚本
功能：
  1. 验证选型主干（bert-base-chinese）可正常加载
  2. 冻结主干，提取 CLS 池化向量，训练轻量逻辑回归头，
     评估在本项目三级标签上的宏平均 F1（80/20 划分，避免过拟合）
  3. 测量单条推理耗时（CPU：原始 vs 动态量化）
  4. 汇总候选模型盘点与选型理由，输出 logs/evaluation_report.json

运行：venv\Scripts\python src\evaluate_models.py
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, load_config, write_log

ROOT = get_project_root()
setup_environment()

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from transformers import BertModel, BertTokenizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, accuracy_score


def extract_cls_vectors(bert, tokenizer, texts, max_len, batch=32):
    """批量提取 CLS 池化向量（冻结主干，无梯度）"""
    vecs = []
    for i in range(0, len(texts), batch):
        enc = tokenizer(texts[i:i + batch], max_length=max_len,
                        padding=True, truncation=True, return_tensors="pt")
        with torch.no_grad():
            out = bert(**enc)
        vecs.append(out.pooler_output.numpy())
    return np.concatenate(vecs, axis=0)


def measure_time(model, tokenizer, text, max_len, n=5):
    """测量单条推理平均耗时（毫秒）"""
    model.eval()
    enc = tokenizer(text, max_length=max_len, padding="max_length",
                    truncation=True, return_tensors="pt")
    with torch.no_grad():
        model(**enc)  # 预热
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        with torch.no_grad():
            model(**enc)
        times.append((time.perf_counter() - t0) * 1000)
    return sum(times) / len(times)


def main():
    config = load_config()
    model_cfg = config["model"]
    ev_cfg = config["evaluation"]
    max_len = int(model_cfg["max_seq_length"])

    write_log("evaluate", "=" * 60)
    write_log("evaluate", "模型评估开始（选定基线：bert-base-chinese）")
    write_log("evaluate", "=" * 60)

    backbone_dir = os.path.join(ROOT, model_cfg["backbone_path"])
    tokenizer = BertTokenizer.from_pretrained(backbone_dir)
    bert = BertModel.from_pretrained(backbone_dir)
    for p in bert.parameters():
        p.requires_grad = False
    write_log("evaluate", f"主干加载成功：hidden={bert.config.hidden_size}, layers={bert.config.num_hidden_layers}")

    # 读取验证集（最多 1000 条）
    val_df = pd.read_csv(os.path.join(ROOT, "data", "processed", "val.csv"))
    if len(val_df) > 1000:
        val_df = val_df.sample(n=1000, random_state=42)
    write_log("evaluate", f"评估样本：{len(val_df)} 条（验证集）")

    with open(os.path.join(ROOT, "data", "processed", "labels.json"), encoding="utf-8") as f:
        labels = json.load(f)

    # 提取 CLS 向量
    X = extract_cls_vectors(bert, tokenizer, val_df["text"].fillna("").tolist(), max_len)
    write_log("evaluate", f"CLS 向量提取完成：{X.shape}")

    # 三头逻辑回归评估（80/20 划分）
    report = {"metrics": {}}
    # labels.json 中的 id 映射键名：subject2id / type2id / knowledge2id
    id_map = {"subject": "subject2id", "question_type": "type2id", "knowledge": "knowledge2id"}
    for key, col in (("subject", "subject"), ("question_type", "question_type"), ("knowledge", "knowledge_point")):
        y = [labels[id_map[key]][v] for v in val_df[col]]
        # 知识点类别多且极不平衡（部分类仅 1 样本），分层会失败，故仅学科/题型用 stratify
        split_kwargs = {"random_state": 42}
        if key != "knowledge":
            split_kwargs["stratify"] = y
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, **split_kwargs)
        clf = LogisticRegression(max_iter=1000)
        clf.fit(X_tr, y_tr)
        pred = clf.predict(X_te)
        f1 = f1_score(y_te, pred, average="macro", zero_division=0)
        acc = accuracy_score(y_te, pred)
        report["metrics"][key] = {"f1_macro": round(float(f1), 4), "accuracy": round(float(acc), 4)}
        write_log("evaluate", f"  {key}: f1_macro={f1:.4f}, acc={acc:.4f}")

    # 推理耗时（CPU 原始 vs 量化）
    sample_text = val_df["text"].iloc[0]
    t_orig = measure_time(bert, tokenizer, sample_text, max_len)
    quantized = torch.quantization.quantize_dynamic(bert, {nn.Linear}, dtype=torch.qint8)
    t_quant = measure_time(quantized, tokenizer, sample_text, max_len)
    report["inference_time_ms"] = {
        "original_cpu": round(float(t_orig), 1),
        "quantized_cpu": round(float(t_quant), 1),
    }
    write_log("evaluate", f"单条推理耗时：原始 {t_orig:.0f}ms / 量化 {t_quant:.0f}ms（门槛 {ev_cfg['max_inference_time_ms']}ms）")

    n_params = sum(p.numel() for p in bert.parameters())
    report["n_params"] = int(n_params)
    report["selected_model"] = "bert-base-chinese"
    report["selection_reason"] = (
        "标准 HuggingFace 中文 BERT base（12 层/768 维），全部候选深度模型的主干来源；"
        "标准格式加载最稳，可冻结 80% 层支持迁移微调与动态量化；"
        "中文预训练通用语义，适合教育题目迁移；CPU 量化后单条推理满足 <1.5s 硬性门槛。"
    )
    report["candidates"] = [
        {"model": "bert-base-chinese (003_bert)", "arch": "BERT 12层/768维", "status": "选中为主干"},
        {"model": "bert_model.pt (003_bert)", "arch": "BERT12 + Linear(768,10) 新闻头", "status": "主干同源，头需剥离，作备选"},
        {"model": "stu_model.pt / distill_model.pt (014_distill)", "arch": "BERT 4层/240维 蒸馏学生", "status": "非标准结构、容量不足，兜底"},
        {"model": "rf_model.pkl / FastText.bin", "arch": "传统ML", "status": "非 Transformer，无法作共享主干"},
        {"model": "012_quantization / 013_pruning *.pt", "arch": "nn.Linear(10000,10000) 演示", "status": "与文本分类无关，排除"},
        {"model": "004_LLM", "arch": "无本地权重", "status": "依赖外部服务，排除"},
    ]
    report["hard_gate_ok"] = report["inference_time_ms"]["quantized_cpu"] < ev_cfg["max_inference_time_ms"]

    report_path = os.path.join(ROOT, "logs", "evaluation_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    write_log("evaluate", f"评估报告已保存：{report_path}")
    write_log("evaluate", f"耗时硬性门槛达标：{report['hard_gate_ok']}")


if __name__ == "__main__":
    main()
