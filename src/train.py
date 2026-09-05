# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 多任务微调训练
流程：
  1. 从 data/processed/ 加载清洗数据与标签映射
  2. 构建多任务模型（共享主干冻结 80% + 三头）
  3. 多任务交叉熵加权训练，验证集早停（patience=3）
  4. 保存：微调主干 → models/pretrained_backbone/bert-base-chinese-finetuned/
          三头 → models/heads/

运行：venv\Scripts\python src\train.py
"""
import os
import sys
import json
import time
import copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, load_config, write_log

ROOT = get_project_root()
setup_environment()

import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import BertTokenizer, get_linear_schedule_with_warmup
from sklearn.metrics import f1_score, accuracy_score

from model import HierarchicalClassifier

# V3: 知识点损失权重进一步提高，Focal Loss gamma 降低，配合 Label Smoothing
LOSS_WEIGHTS = {"subject": 0.28, "question_type": 0.27, "knowledge": 0.45}


class FocalLossLabelSmooth(nn.Module):
    """Focal Loss + Label Smoothing 结合：同时处理类别不均衡和过度自信"""

    def __init__(self, alpha: float = 1.0, gamma: float = 1.5, label_smooth: float = 0.08):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.label_smooth = label_smooth

    def forward(self, logits, targets):
        n_cls = logits.size(-1)
        # Label smoothing: 将 one-hot 标签变为 (1-ε) + ε/n
        with torch.no_grad():
            smooth_targets = torch.zeros_like(logits).scatter_(
                1, targets.unsqueeze(1), 1.0 - self.label_smooth
            ) + self.label_smooth / n_cls
        # 计算 log_softmax
        log_probs = nn.functional.log_softmax(logits, dim=-1)
        # 平滑交叉熵（每个样本）
        ce_per_sample = -(smooth_targets * log_probs).sum(dim=-1)
        # Focal 调制：用正确类概率 pt
        probs = torch.exp(log_probs)
        pt = probs.gather(1, targets.unsqueeze(1)).squeeze(1)
        loss = self.alpha * (1.0 - pt) ** self.gamma * ce_per_sample
        return loss.mean()


class LabelSmoothCE(nn.Module):
    """带 Label Smoothing 的交叉熵（用于学科/题型头，类别较少时平滑效果更好）"""

    def __init__(self, label_smooth: float = 0.05):
        super().__init__()
        self.label_smooth = label_smooth

    def forward(self, logits, targets):
        n_cls = logits.size(-1)
        with torch.no_grad():
            smooth_targets = torch.zeros_like(logits).scatter_(
                1, targets.unsqueeze(1), 1.0 - self.label_smooth
            ) + self.label_smooth / n_cls
        log_probs = nn.functional.log_softmax(logits, dim=-1)
        return -(smooth_targets * log_probs).sum(dim=-1).mean()


class QuestionDataset(Dataset):
    """题目分类数据集"""

    def __init__(self, df, tokenizer, labels, max_len):
        self.texts = df["text"].fillna("").tolist()
        self.subjects = [labels["subject2id"][s] for s in df["subject"]]
        self.types = [labels["type2id"][t] for t in df["question_type"]]
        self.knowledge = [labels["knowledge2id"][k] for k in df["knowledge_point"]]
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        enc = self.tokenizer(
            self.texts[idx],
            max_length=self.max_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "subject": torch.tensor(self.subjects[idx], dtype=torch.long),
            "question_type": torch.tensor(self.types[idx], dtype=torch.long),
            "knowledge": torch.tensor(self.knowledge[idx], dtype=torch.long),
        }


def evaluate(model, dataloader, device):
    """验证/测试：返回三个头的宏平均 F1 与准确率"""
    model.eval()
    preds = {"subject": [], "question_type": [], "knowledge": []}
    trues = {"subject": [], "question_type": [], "knowledge": []}
    with torch.no_grad():
        for batch in dataloader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            out = model(input_ids, attention_mask)
            for k in preds:
                preds[k].extend(out[k].argmax(dim=-1).cpu().tolist())
                trues[k].extend(batch[k].tolist())
    result = {}
    for k in preds:
        result[f"{k}_acc"] = accuracy_score(trues[k], preds[k])
        result[f"{k}_f1"] = f1_score(trues[k], preds[k], average="macro", zero_division=0)
    result["mean_f1"] = (result["subject_f1"] + result["question_type_f1"] + result["knowledge_f1"]) / 3
    return result


def train():
    config = load_config()
    t_cfg = config["training"]
    model_cfg = config["model"]

    proc_dir = os.path.join(ROOT, "data", "processed")
    labels_path = os.path.join(proc_dir, "labels.json")
    with open(labels_path, encoding="utf-8") as f:
        labels = json.load(f)

    n_subjects = len(labels["subjects"])
    n_types = len(labels["question_types"])
    n_knowledge = len(labels["knowledge_points"])
    write_log("train", f"标签规模：学科 {n_subjects} / 题型 {n_types} / 知识点 {n_knowledge}")

    backbone_dir = os.path.join(ROOT, model_cfg["backbone_path"])
    tokenizer = BertTokenizer.from_pretrained(backbone_dir)
    write_log("train", f"Tokenizer 加载完成：vocab={len(tokenizer)}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    write_log("train", f"设备：{device}")

    train_df = pd.read_csv(os.path.join(proc_dir, "train.csv"))
    val_df = pd.read_csv(os.path.join(proc_dir, "val.csv"))
    write_log("train", f"训练 {len(train_df)} / 验证 {len(val_df)}")

    max_len = int(model_cfg["max_seq_length"])
    train_ds = QuestionDataset(train_df, tokenizer, labels, max_len)
    val_ds = QuestionDataset(val_df, tokenizer, labels, max_len)
    train_loader = DataLoader(train_ds, batch_size=t_cfg["batch_size"], shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=t_cfg["batch_size"], shuffle=False, num_workers=0)

    model = HierarchicalClassifier(
        backbone_dir,
        n_subjects=n_subjects,
        n_types=n_types,
        n_knowledge=n_knowledge,
        freeze_ratio=float(model_cfg["freeze_layers"]),
    ).to(device)

    # 只更新需训练参数（冻结层不参与）
    trainable = [p for p in model.parameters() if p.requires_grad]
    n_total = sum(p.numel() for p in model.parameters())
    n_tuned = sum(p.numel() for p in trainable)
    write_log("train", f"参数量：总 {n_total/1e6:.1f}M，可训练 {n_tuned/1e6:.1f}M（{(100*n_tuned/n_total):.1f}%）")

    optimizer = torch.optim.AdamW(trainable, lr=float(t_cfg["learning_rate"]), weight_decay=float(t_cfg["weight_decay"]))
    total_steps = len(train_loader) * t_cfg["epochs"]
    warmup_steps = int(total_steps * t_cfg["warmup_ratio"])
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    # V3: 三个头使用不同损失函数
    # 学科/题型：类别少，用 LabelSmoothCE（防止过度自信）
    # 知识点：类别多且细，用 FocalLoss + LabelSmooth（兼顾难样本 + 置信度校准）
    loss_subject = LabelSmoothCE(label_smooth=0.05)
    loss_type = LabelSmoothCE(label_smooth=0.05)
    loss_knowledge = FocalLossLabelSmooth(alpha=1.0, gamma=1.5, label_smooth=0.08)

    # 早停
    patience = int(t_cfg["early_stopping_patience"])
    best_f1 = -1.0
    best_state = None
    bad_epochs = 0
    log_path = os.path.join(ROOT, "logs", "training.log")
    logf = open(log_path, "a", encoding="utf-8")

    start = time.time()
    for epoch in range(1, t_cfg["epochs"] + 1):
        model.train()
        epoch_loss = 0.0
        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            out = model(input_ids, attention_mask)
            loss = 0.0
            # V3: 分别用不同的损失函数
            loss_s = LOSS_WEIGHTS["subject"] * loss_subject(out["subject"], batch["subject"].to(device))
            loss_t = LOSS_WEIGHTS["question_type"] * loss_type(out["question_type"], batch["question_type"].to(device))
            loss_k = LOSS_WEIGHTS["knowledge"] * loss_knowledge(out["knowledge"], batch["knowledge"].to(device))
            loss = loss_s + loss_t + loss_k
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(trainable, max_norm=1.0)
            optimizer.step()
            scheduler.step()
            epoch_loss += loss.item()
            if (step + 1) % 20 == 0:
                msg = f"epoch {epoch}/{t_cfg['epochs']} step {step+1}/{len(train_loader)} loss {epoch_loss/(step+1):.4f}"
                write_log("train", msg)
                logf.write(msg + "\n")
                logf.flush()

        # 验证
        val_metric = evaluate(model, val_loader, device)
        mean_f1 = val_metric["mean_f1"]
        msg = (f"epoch {epoch} 完成 | 验证 学科 acc {val_metric['subject_acc']:.4f} f1 {val_metric['subject_f1']:.4f} | "
               f"题型 f1 {val_metric['question_type_f1']:.4f} | 知识点 f1 {val_metric['knowledge_f1']:.4f} | mean_f1 {mean_f1:.4f} | "
               f"耗时 {time.time()-start:.0f}s")
        write_log("train", msg)
        logf.write(msg + "\n")
        logf.flush()

        # 早停判断
        if mean_f1 > best_f1:
            best_f1 = mean_f1
            bad_epochs = 0
            best_state = {
                "bert": copy.deepcopy(model.bert.state_dict()),
                "subject": copy.deepcopy(model.subject_head.state_dict()),
                "type": copy.deepcopy(model.type_head.state_dict()),
                "knowledge": copy.deepcopy(model.knowledge_head.state_dict()),
            }
            write_log("train", f"  ↳ 保存最佳模型（mean_f1={best_f1:.4f}）")
        else:
            bad_epochs += 1
            write_log("train", f"  ↳ 验证 F1 未提升（连续 {bad_epochs}/{patience}）")
            if bad_epochs >= patience:
                write_log("train", f"早停触发于 epoch {epoch}")
                break

    logf.close()

    # 保存最佳模型
    if best_state is None:
        best_state = {
            "bert": model.bert.state_dict(),
            "subject": model.subject_head.state_dict(),
            "type": model.type_head.state_dict(),
            "knowledge": model.knowledge_head.state_dict(),
        }
    finetuned_dir = os.path.join(ROOT, "models", "pretrained_backbone", "bert-base-chinese-finetuned")
    os.makedirs(finetuned_dir, exist_ok=True)
    torch.save(best_state["bert"], os.path.join(finetuned_dir, "pytorch_model.bin"))
    torch.save(model.bert.config, os.path.join(finetuned_dir, "config.pt"))

    heads_dir = os.path.join(ROOT, "models", "heads")
    os.makedirs(heads_dir, exist_ok=True)
    torch.save(best_state["subject"], os.path.join(heads_dir, "subject_head.pt"))
    torch.save(best_state["type"], os.path.join(heads_dir, "type_head.pt"))
    torch.save(best_state["knowledge"], os.path.join(heads_dir, "knowledge_head.pt"))
    write_log("train", f"训练完成，最佳 mean_f1={best_f1:.4f}，模型已保存")
    write_log("train", f"  主干 → {finetuned_dir}/pytorch_model.bin")
    write_log("train", f"  三头 → {heads_dir}/")


if __name__ == "__main__":
    train()
