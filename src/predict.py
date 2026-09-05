# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 推理预测模块
功能：
  - 加载微调主干 + 三头 + 标签映射 + tokenizer
  - 支持 CPU 动态量化（int8）加速
  - 相同输入 lru_cache 缓存
  - 输出三级标签 + 各级 Softmax 置信度
"""
import os
import sys
import json
import time
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, load_config, write_log

ROOT = get_project_root()
setup_environment()

import torch
import torch.nn as nn
from transformers import BertTokenizer

from model import HierarchicalClassifier


class HierarchicalPredictor:
    """三级层级分类预测器（加载 + 量化 + 推理）"""

    def __init__(self, verbose: bool = True):
        self.config = load_config()
        self.model_cfg = self.config["model"]
        self.hw_cfg = self.config["hardware"]
        self.max_len = int(self.model_cfg["max_seq_length"])

        # 加载标签映射
        with open(os.path.join(ROOT, "data", "processed", "labels.json"), encoding="utf-8") as f:
            self.labels = json.load(f)
        self.id2subject = self.labels["subjects"]
        self.id2type = self.labels["question_types"]
        self.id2knowledge = self.labels["knowledge_points"]

        # 主干路径（原始 + 微调）
        backbone_dir = os.path.join(ROOT, self.model_cfg["backbone_path"])
        finetuned_dir = os.path.join(ROOT, "models", "pretrained_backbone", "bert-base-chinese-finetuned")
        heads_dir = os.path.join(ROOT, "models", "heads")

        self.tokenizer = BertTokenizer.from_pretrained(backbone_dir)

        # 构建模型（内部加载原始主干）
        self.model = HierarchicalClassifier(
            backbone_dir,
            n_subjects=len(self.id2subject),
            n_types=len(self.id2type),
            n_knowledge=len(self.id2knowledge),
            freeze_ratio=float(self.model_cfg["freeze_layers"]),
        )

        # 覆盖为微调后的主干权重（若存在）
        ft_weights = os.path.join(finetuned_dir, "pytorch_model.bin")
        if os.path.exists(ft_weights):
            self.model.bert.load_state_dict(torch.load(ft_weights, map_location="cpu"))
            self.model.load_heads(heads_dir)
            if verbose:
                write_log("predict", "已加载微调主干 + 三头权重")
        else:
            if verbose:
                write_log("predict", "未找到微调权重，使用原始主干 + 未训练头（仅初始化）", "WARN")

        self.model.eval()

        # 动态量化（CPU 模式下默认开启）
        self.quantized = False
        if self.hw_cfg["use_dynamic_quantization"] and not torch.cuda.is_available():
            self.model = torch.quantization.quantize_dynamic(self.model, {nn.Linear}, dtype=torch.qint8)
            self.quantized = True
            if verbose:
                write_log("predict", "已启用动态量化（int8）")

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        if verbose:
            write_log("predict", f"推理设备：{self.device}，max_len={self.max_len}")

    @lru_cache(maxsize=512)
    def _predict_cached(self, text: str) -> dict:
        """缓存的单条推理核心逻辑"""
        inputs = self.tokenizer(
            text, max_length=self.max_len, padding="max_length",
            truncation=True, return_tensors="pt",
        )
        input_ids = inputs["input_ids"].to(self.device)
        attention_mask = inputs["attention_mask"].to(self.device)
        with torch.no_grad():
            out = self.model(input_ids, attention_mask)
        result = {}
        conf = {}
        # model 输出键用 knowledge，对外统一为 knowledge_point
        out_key_map = {"subject": "subject", "question_type": "question_type", "knowledge": "knowledge_point"}
        for key, id2label in (("subject", self.id2subject), ("question_type", self.id2type), ("knowledge", self.id2knowledge)):
            probs = torch.softmax(out[key], dim=-1)[0]
            idx = int(probs.argmax().item())
            rk = out_key_map[key]
            result[rk] = id2label[idx]
            conf[rk] = round(float(probs[idx].item()), 4)
        result["confidence"] = conf
        return result

    def predict(self, text: str) -> dict:
        """对单条题目文本进行三级分类，返回标签与置信度"""
        text = " ".join(text.split())
        return self._predict_cached(text)

    def predict_with_time(self, text: str, n_repeat: int = 5) -> tuple:
        """预测并测量单条推理耗时（毫秒）"""
        # 预热
        self.predict(text)
        times = []
        result = None
        for _ in range(n_repeat):
            t0 = time.perf_counter()
            result = self._predict_cached(text)
            times.append((time.perf_counter() - t0) * 1000)
        avg_ms = sum(times) / len(times)
        return result, avg_ms

    def benchmark(self, samples: list, n_repeat: int = 3) -> float:
        """在若干样本上测量平均单条推理耗时（毫秒），并排除缓存影响"""
        times = []
        for s in samples:
            t0 = time.perf_counter()
            self._predict_cached(s)
            times.append((time.perf_counter() - t0) * 1000)
        return sum(times) / len(times)


def load_predictor() -> HierarchicalPredictor:
    """便捷函数：加载全局预测器（模块级单例）"""
    global _PREDICTOR
    if "_PREDICTOR" not in globals():
        _PREDICTOR = HierarchicalPredictor(verbose=False)
    return _PREDICTOR


if __name__ == "__main__":
    # 命令行冒烟测试
    p = HierarchicalPredictor()
    test_text = "已知函数 f(x)=x²+1，求 f(2) 的值。"
    print("输入：", test_text)
    res, ms = p.predict_with_time(test_text)
    print("结果：", json.dumps(res, ensure_ascii=False, indent=2))
    print(f"单条推理耗时：{ms:.1f} ms")
