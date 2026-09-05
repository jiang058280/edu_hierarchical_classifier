"""多任务层级分类模型（自 src/model.py 平移）。

结构：共享 BERT 主干（冻结前 75% 层）+ 三个独立分类头（学科 Linear / 题型 Linear /
知识点 ResidualMLP），一次前向同时得到三级结果。

与旧版的差异：
- 移除模块级的 os.chdir / sys.path 注入等副作用（旧版 import 即改工作目录）；
- 日志改用 edu_core.config.logging_config；
- save_heads / load_heads 增加 weights_only=True（torch>=2.6 安全加载默认值），
  只加载张量 state_dict，不执行任意 pickle。
"""

from __future__ import annotations

import os

import torch
import torch.nn as nn
from transformers import BertModel

from edu_core.config.logging_config import get_logger

logger = get_logger(__name__)


class ResidualMLPHead(nn.Module):
    """残差 MLP 分类头：双层残差结构 + LayerNorm，细粒度分类更强（用于知识点）。"""

    def __init__(self, hidden_size: int, n_classes: int, dropout: float = 0.25,
                 mid_dim: int = 384, low_dim: int = 128):
        super().__init__()
        self.fc1 = nn.Linear(hidden_size, mid_dim)
        self.ln1 = nn.LayerNorm(mid_dim)
        self.act1 = nn.GELU()
        self.drop1 = nn.Dropout(dropout)

        # 残差块：mid_dim -> low_dim -> mid_dim
        self.block_fc1 = nn.Linear(mid_dim, low_dim)
        self.block_ln1 = nn.LayerNorm(low_dim)
        self.block_act = nn.GELU()
        self.block_drop1 = nn.Dropout(dropout)
        self.block_fc2 = nn.Linear(low_dim, mid_dim)
        self.block_ln2 = nn.LayerNorm(mid_dim)
        self.block_drop2 = nn.Dropout(dropout / 2)

        self.fc_out = nn.Linear(mid_dim, n_classes)

    def forward(self, x):
        h = self.drop1(self.act1(self.ln1(self.fc1(x))))
        residual = h
        h2 = self.block_drop1(self.block_act(self.block_ln1(self.block_fc1(h))))
        h2 = self.block_drop2(self.block_ln2(self.block_fc2(h2)))
        h = residual + h2
        return self.fc_out(h)


class HierarchicalClassifier(nn.Module):
    """共享主干 + 三头多任务层级分类器。"""

    def __init__(self, backbone_dir: str, n_subjects: int, n_types: int,
                 n_knowledge: int, freeze_ratio: float = 0.75):
        super().__init__()
        logger.info("加载主干：%s", backbone_dir)
        self.bert = BertModel.from_pretrained(backbone_dir)

        n_layers = self.bert.config.num_hidden_layers
        freeze_layers = int(n_layers * freeze_ratio)
        for p in self.bert.embeddings.parameters():
            p.requires_grad = False
        for i in range(freeze_layers):
            for p in self.bert.encoder.layer[i].parameters():
                p.requires_grad = False
        logger.info(
            "冻结策略：embeddings + 前 %s/%s 层冻结，后 %s 层 + pooler 微调",
            freeze_layers, n_layers, n_layers - freeze_layers,
        )

        hidden = self.bert.config.hidden_size
        self.subject_head = nn.Linear(hidden, n_subjects)
        self.type_head = nn.Linear(hidden, n_types)
        self.knowledge_head = ResidualMLPHead(hidden, n_knowledge,
                                              dropout=0.25, mid_dim=384, low_dim=128)
        logger.info(
            "分类头：学科 Linear(%s->%s) / 题型 Linear(%s->%s) / 知识点 ResidualMLP(%s->384<->128->%s)",
            hidden, n_subjects, hidden, n_types, hidden, n_knowledge,
        )
        self._init_heads()

    def _init_heads(self) -> None:
        """分类头统一 xavier 初始化（兼容 Linear / ResidualMLPHead）。"""
        for head in (self.subject_head, self.type_head, self.knowledge_head):
            for module in head.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)

    def forward(self, input_ids, attention_mask=None, token_type_ids=None):
        outputs = self.bert(input_ids=input_ids,
                            attention_mask=attention_mask,
                            token_type_ids=token_type_ids)
        pooled = outputs.pooler_output
        return {
            "subject": self.subject_head(pooled),
            "question_type": self.type_head(pooled),
            "knowledge": self.knowledge_head(pooled),
        }

    def save_heads(self, save_dir: str) -> None:
        """保存三个分类头权重。"""
        os.makedirs(save_dir, exist_ok=True)
        torch.save(self.subject_head.state_dict(), os.path.join(save_dir, "subject_head.pt"))
        torch.save(self.type_head.state_dict(), os.path.join(save_dir, "type_head.pt"))
        torch.save(self.knowledge_head.state_dict(), os.path.join(save_dir, "knowledge_head.pt"))
        logger.info("三头已保存至 %s", save_dir)

    def load_heads(self, save_dir: str) -> None:
        """加载三个分类头权重（weights_only=True，只接受张量 state_dict）。"""
        self.subject_head.load_state_dict(
            torch.load(os.path.join(save_dir, "subject_head.pt"), map_location="cpu", weights_only=True))
        self.type_head.load_state_dict(
            torch.load(os.path.join(save_dir, "type_head.pt"), map_location="cpu", weights_only=True))
        self.knowledge_head.load_state_dict(
            torch.load(os.path.join(save_dir, "knowledge_head.pt"), map_location="cpu", weights_only=True))
        logger.info("三头已从 %s 加载", save_dir)
