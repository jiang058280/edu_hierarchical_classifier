# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 多任务层级分类模型
结构：共享 BERT 主干（冻结前 80% 层）+ 三个独立分类头（学科/题型/知识点）
一次前向同时得到三级分类结果，避免串行级联耗时。
"""
import torch
import torch.nn as nn
from transformers import BertModel

from utils import get_project_root, setup_environment, write_log

ROOT = get_project_root()
setup_environment()


class ResidualMLPHead(nn.Module):
    """残差 MLP 分类头：双层残差结构 + LayerNorm，细粒度分类更强（用于知识点）"""

    def __init__(self, hidden_size: int, n_classes: int, dropout: float = 0.25,
                 mid_dim: int = 384, low_dim: int = 128):
        super().__init__()
        # 第一层：压缩到 mid_dim
        self.fc1 = nn.Linear(hidden_size, mid_dim)
        self.ln1 = nn.LayerNorm(mid_dim)
        self.act1 = nn.GELU()
        self.drop1 = nn.Dropout(dropout)

        # 残差块：mid_dim → low_dim → mid_dim
        self.block_fc1 = nn.Linear(mid_dim, low_dim)
        self.block_ln1 = nn.LayerNorm(low_dim)
        self.block_act = nn.GELU()
        self.block_drop1 = nn.Dropout(dropout)
        self.block_fc2 = nn.Linear(low_dim, mid_dim)
        self.block_ln2 = nn.LayerNorm(mid_dim)
        self.block_drop2 = nn.Dropout(dropout / 2)

        # 最终分类层
        self.fc_out = nn.Linear(mid_dim, n_classes)

    def forward(self, x):
        h = self.drop1(self.act1(self.ln1(self.fc1(x))))
        # 残差连接
        residual = h
        h2 = self.block_drop1(self.block_act(self.block_ln1(self.block_fc1(h))))
        h2 = self.block_drop2(self.block_ln2(self.block_fc2(h2)))
        h = residual + h2  # 残差相加
        return self.fc_out(h)


class HierarchicalClassifier(nn.Module):
    """共享主干 + 三头多任务层级分类器（知识点头升级为残差 MLP）"""

    def __init__(self, backbone_dir: str, n_subjects: int, n_types: int,
                 n_knowledge: int, freeze_ratio: float = 0.75):
        super().__init__()
        write_log("model", f"加载主干：{backbone_dir}")
        self.bert = BertModel.from_pretrained(backbone_dir)

        # V3: 降低冻结比例，多解冻 5% 的 BERT 层（前 75% 冻结，后 25% 微调）
        n_layers = self.bert.config.num_hidden_layers
        freeze_layers = int(n_layers * freeze_ratio)
        for p in self.bert.embeddings.parameters():
            p.requires_grad = False
        for i in range(freeze_layers):
            for p in self.bert.encoder.layer[i].parameters():
                p.requires_grad = False
        write_log("model",
                  f"冻结策略 V3：embeddings + 前 {freeze_layers}/{n_layers} 层冻结，后 {n_layers - freeze_layers} 层 + pooler 微调")

        hidden = self.bert.config.hidden_size
        # 学科头、题型头：简单 Linear（类别少，无需 MLP）
        self.subject_head = nn.Linear(hidden, n_subjects)
        self.type_head = nn.Linear(hidden, n_types)
        # 知识点头：升级为残差 MLP（768→384↔128→n），表达能力大幅提升
        self.knowledge_head = ResidualMLPHead(hidden, n_knowledge,
                                              dropout=0.25, mid_dim=384, low_dim=128)
        write_log("model",
                  f"分类头 V3：学科 Linear({hidden}→{n_subjects}) / 题型 Linear({hidden}→{n_types}) / "
                  f"知识点 ResidualMLP({hidden}→384↔128→{n_knowledge})")

        self._init_heads()

    def _init_heads(self):
        """分类头权重初始化（兼容 Linear / MLPHead / ResidualMLPHead）"""
        for head in (self.subject_head, self.type_head, self.knowledge_head):
            # 遍历模块下所有 Linear 层，统一 xavier 初始化
            for module in head.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
                    if module.bias is not None:
                        nn.init.zeros_(module.bias)

    def forward(self, input_ids, attention_mask=None, token_type_ids=None):
        outputs = self.bert(input_ids=input_ids,
                            attention_mask=attention_mask,
                            token_type_ids=token_type_ids)
        pooled = outputs.pooler_output  # [B, hidden]，CLS 池化向量
        return {
            "subject": self.subject_head(pooled),
            "question_type": self.type_head(pooled),
            "knowledge": self.knowledge_head(pooled),
        }

    def save_heads(self, save_dir: str):
        """保存三个分类头权重"""
        import os
        os.makedirs(save_dir, exist_ok=True)
        torch.save(self.subject_head.state_dict(), os.path.join(save_dir, "subject_head.pt"))
        torch.save(self.type_head.state_dict(), os.path.join(save_dir, "type_head.pt"))
        torch.save(self.knowledge_head.state_dict(), os.path.join(save_dir, "knowledge_head.pt"))
        write_log("model", f"三头已保存至 {save_dir}")

    def load_heads(self, save_dir: str):
        """加载三个分类头权重"""
        import os
        self.subject_head.load_state_dict(torch.load(os.path.join(save_dir, "subject_head.pt"), map_location="cpu"))
        self.type_head.load_state_dict(torch.load(os.path.join(save_dir, "type_head.pt"), map_location="cpu"))
        self.knowledge_head.load_state_dict(torch.load(os.path.join(save_dir, "knowledge_head.pt"), map_location="cpu"))
        write_log("model", f"三头已从 {save_dir} 加载")
