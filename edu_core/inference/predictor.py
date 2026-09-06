"""层级分类预测器（自 src/predict.py 平移并修复缺陷）。

相对旧版的修复（对应《企业级改造计划》8.5 节）：

1. 消灭静默降级：版本目录三头权重或 manifest 缺失时抛 ModelArtifactError，
   不再退回"随机初始化的头对外服务"；
2. 移除挂在实例方法上的 @lru_cache（强引用 self、多 worker 命中率归零），
   改为进程内有界 OrderedDict LRU；
3. BertTokenizer（已弃用）-> AutoTokenizer；
4. torch.load 全部 weights_only=True；
5. 新增 embed()：输出 BERT [CLS] 池化向量，供 Milvus 题目查重使用。

预测器必须显式传入模型版本目录（governance 解析 active 版本后注入），
由 application.factory 进程级单例管理。
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path

import torch
import torch.nn as nn
from transformers import AutoTokenizer

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings, get_settings
from edu_core.inference.model import HierarchicalClassifier

logger = get_logger(__name__)

# torch>=2.x 官方位置是 torch.ao.quantization；旧别名 torch.quantization 在部分版本移除
try:
    from torch.ao.quantization import quantize_dynamic
except ImportError:  # pragma: no cover - 兼容老版本 torch
    from torch.quantization import quantize_dynamic


class ModelArtifactError(RuntimeError):
    """模型产物缺失或不一致：拒绝服务，绝不带随机权重上线。"""


def build_knowledge_mask(labels: dict, device: torch.device | None = None) -> torch.Tensor:
    """构建知识点非法组合掩码（改进计划 WP-G1，纯函数便于测试）。

    返回 [n_subjects, n_knowledge] 布尔张量：True = 该学科下**非法**的知识点
    （由 labels.subject_knowledge 合法组合表驱动，未列出的组合视为非法）。
    """
    subjects = labels["subjects"]
    knowledge = labels["knowledge_points"]
    subject_knowledge = labels.get("subject_knowledge", {})
    mask = torch.zeros(len(subjects), len(knowledge), dtype=torch.bool)
    for s_idx, subject in enumerate(subjects):
        legal = set(subject_knowledge.get(subject, []))
        for k_idx, kp in enumerate(knowledge):
            mask[s_idx][k_idx] = kp not in legal
    if device is not None:
        mask = mask.to(device)
    return mask


class HierarchicalPredictor:
    """三级层级分类预测器（版本化加载 + 量化 + 推理 + 向量提取）。"""

    def __init__(self, version_dir: str | Path, settings: Settings | None = None,
                 verbose: bool = True):
        self.settings = settings or get_settings()
        self.version_dir = Path(version_dir)
        self.model_version = self.version_dir.name
        self.max_len = int(self.settings.max_seq_length)

        # ---------- 版本 manifest：版本元数据与 backbone_ref ----------
        manifest_path = self.version_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ModelArtifactError(f"manifest.json 不存在：{self.version_dir}（拒绝启动）")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # ---------- 主干与权重分工 ----------
        # tokenizer / 模型结构：settings.backbone_dir（原始主干，含 tokenizer 与 config）；
        # 权重覆盖：manifest.backbone_ref（微调产物目录，只需 pytorch_model.bin）。
        # 两目录分离：finetuned 产物目录历来只存权重，不复制 tokenizer 文件。
        base_backbone_dir = self.settings.abs_path(self.settings.backbone_dir)
        if not base_backbone_dir.is_dir():
            raise ModelArtifactError(f"原始主干目录不存在：{base_backbone_dir}")
        backbone_ref = self.manifest.get("backbone_ref")
        if not backbone_ref:
            raise ModelArtifactError(f"manifest.json 缺少 backbone_ref：{self.version_dir}")
        finetuned_dir = Path(backbone_ref)
        if not finetuned_dir.is_absolute():
            finetuned_dir = self.settings.root(finetuned_dir)
        if not finetuned_dir.is_dir():
            raise ModelArtifactError(f"微调主干目录不存在：{finetuned_dir}")

        # ---------- 标签映射 ----------
        labels_path = self.settings.abs_path(self.settings.data_processed_dir) / "labels.json"
        if not labels_path.is_file():
            raise ModelArtifactError(f"标签映射文件不存在：{labels_path}")
        with open(labels_path, encoding="utf-8") as f:
            self.labels = json.load(f)
        self.id2subject = self.labels["subjects"]
        self.id2type = self.labels["question_types"]
        self.id2knowledge = self.labels["knowledge_points"]

        # ---------- tokenizer（原始主干目录）+ 模型（结构取原始主干，权重覆盖为微调） ----------
        self.tokenizer = AutoTokenizer.from_pretrained(str(base_backbone_dir))
        # 架构声明（改进计划 WP-G2 + 数据扩充轮）：
        #   subject_embedding_dim>0 = 知识头注入学科 embedding；
        #   grade_head=true = 增加学段头（初中/高中）。
        # 旧版本 manifest 无这些字段 → 旧结构，向后兼容。
        arch = self.manifest.get("architecture") or {}
        self.subject_embedding_dim = int(arch.get("subject_embedding_dim", 0))
        grade_head_flag = bool(arch.get("grade_head", False))
        self.id2grade = list(self.labels.get("grade_bands", [])) if grade_head_flag else []
        self.model = HierarchicalClassifier(
            str(base_backbone_dir),
            n_subjects=len(self.id2subject),
            n_types=len(self.id2type),
            n_knowledge=len(self.id2knowledge),
            freeze_ratio=float(self.settings.freeze_ratio),
            subject_embedding_dim=self.subject_embedding_dim,
            n_grade_bands=len(self.id2grade),
        )
        # 主干覆盖为微调权重（fail-fast：文件缺失即拒绝启动，绝不带随机头服务）
        ft_weights = finetuned_dir / "pytorch_model.bin"
        if not ft_weights.is_file():
            raise ModelArtifactError(f"微调主干权重缺失：{ft_weights}")
        self.model.bert.load_state_dict(
            torch.load(ft_weights, map_location="cpu", weights_only=True))
        self.model.load_heads(str(self.version_dir))
        if verbose:
            logger.info("已加载模型版本 %s（微调权重：%s）", self.model_version, finetuned_dir)

        self.model.eval()

        # ---------- 动态量化（仅 CPU）----------
        self.quantized = False
        if self.settings.use_dynamic_quantization and not torch.cuda.is_available():
            self.model = quantize_dynamic(self.model, {nn.Linear}, dtype=torch.qint8)
            self.quantized = True
            if verbose:
                logger.info("已启用动态量化（int8）")

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        if verbose:
            logger.info("推理设备：%s，max_len=%s，版本=%s", self.device, self.max_len, self.model_version)

        # ---------- 进程内有界 LRU 缓存（替代旧版实例 lru_cache） ----------
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_maxsize = int(self.settings.predict_cache_size)

        # ---------- 知识点学科 mask（WP-G1）：非法组合 logits 置 -inf ----------
        self.knowledge_mask_enabled = bool(self.settings.knowledge_mask_enabled)
        self.knowledge_mask = build_knowledge_mask(self.labels, device=self.device)
        n_legal = int((~self.knowledge_mask).sum())
        if verbose:
            logger.info("知识点学科 mask：%s（合法组合 %s/%s）",
                        "启用" if self.knowledge_mask_enabled else "关闭",
                        n_legal, self.knowledge_mask.numel())

        # ---------- 置信度温度（改进计划 WP-G3）：logits/T 后再 softmax ----------
        # 由 scripts/fit_temperature.py 在验证集上拟合后写入版本 manifest；缺省 1.0（不校准）
        self.temperature = float(self.manifest.get("temperature", 1.0))
        if verbose and self.temperature != 1.0:
            logger.info("置信度校准温度 T=%.4f", self.temperature)

    # ------------------------------------------------------------------
    # 核心推理
    # ------------------------------------------------------------------
    def _forward(self, text: str) -> dict:
        """单条前向：三级 softmax -> 标签 + 置信度（知识点可按学科 mask）。"""
        inputs = self.tokenizer(
            text, max_length=self.max_len, padding="max_length",
            truncation=True, return_tensors="pt",
        )
        input_ids = inputs["input_ids"].to(self.device)
        attention_mask = inputs["attention_mask"].to(self.device)
        with torch.no_grad():
            out = self.model(input_ids, attention_mask)
        result: dict = {}
        conf: dict = {}
        subject_idx: int | None = None
        heads = [("subject", self.id2subject), ("question_type", self.id2type),
                 ("knowledge", self.id2knowledge)]
        if self.id2grade:
            heads.append(("grade", self.id2grade))
        for key, id2label in heads:
            logits = out[key] / self.temperature
            if key == "knowledge":
                if subject_idx is None:
                    # 学科先行：mask 依赖本次预测的学科
                    subject_idx = int(torch.softmax(out["subject"], dim=-1)[0].argmax().item())
                if self.knowledge_mask_enabled:
                    logits = logits.masked_fill(self.knowledge_mask[subject_idx], float("-inf"))
            probs = torch.softmax(logits, dim=-1)[0]
            idx = int(probs.argmax().item())
            result["grade_band" if key == "grade" else key] = id2label[idx]
            conf[key] = round(float(probs[idx].item()), 4)
        result["confidence"] = conf
        return result

    def predict(self, text: str) -> dict:
        """对单条题目文本三级分类，返回标签/置信度/耗时（带进程内 LRU 缓存）。

        返回字段（对外统一命名，兼容旧前端契约）：
          subject / question_type / knowledge_point / confidence{subject,question_type,knowledge_point}
          / latency_ms / cached / model_version
        """
        normalized = " ".join(text.split())
        hit = self._cache.get(normalized)
        if hit is not None:
            self._cache.move_to_end(normalized)
            return {**hit, "cached": True, "latency_ms": 0.0}

        started = time.perf_counter()
        raw = self._forward(normalized)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        result = {
            "subject": raw["subject"],
            "question_type": raw["question_type"],
            "knowledge_point": raw["knowledge"],
            "confidence": {
                "subject": raw["confidence"]["subject"],
                "question_type": raw["confidence"]["question_type"],
                "knowledge_point": raw["confidence"]["knowledge"],
            },
            "latency_ms": latency_ms,
            "cached": False,
            "model_version": self.model_version,
        }
        if "grade_band" in raw:
            result["grade_band"] = raw["grade_band"]
            result["confidence"]["grade"] = raw["confidence"]["grade"]
        self._cache[normalized] = result
        if len(self._cache) > self._cache_maxsize:
            self._cache.popitem(last=False)
        return result

    def embed(self, text: str) -> list[float]:
        """题目文本的 BERT [CLS] 池化向量（768 维），供 Milvus 语义查重。"""
        inputs = self.tokenizer(
            text, max_length=self.max_len, padding="max_length",
            truncation=True, return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self.model.bert(
                input_ids=inputs["input_ids"].to(self.device),
                attention_mask=inputs["attention_mask"].to(self.device),
            )
        return outputs.pooler_output[0].tolist()

    # ------------------------------------------------------------------
    # 批量与基准
    # ------------------------------------------------------------------
    def predict_batch(self, texts: list[str]) -> list[dict]:
        """批量预测（逐条复用 predict 的缓存与计时逻辑）。"""
        return [self.predict(t) for t in texts]

    def benchmark(self, samples: list[str], n_repeat: int = 3) -> float:
        """测量平均单条推理耗时（ms）；重复同样本无缓存意义，走绕缓存前向。"""
        times = []
        for s in samples:
            normalized = " ".join(s.split())
            for _ in range(n_repeat):
                started = time.perf_counter()
                self._forward(normalized)
                times.append((time.perf_counter() - started) * 1000)
        return round(sum(times) / len(times), 2)
