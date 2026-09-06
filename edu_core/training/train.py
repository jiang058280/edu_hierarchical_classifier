"""多任务微调训练（自 src/train.py 平移）。

流程：
  1. 从 data/processed/ 加载清洗数据与标签映射
  2. 构建多任务模型（共享主干按比例冻结 + 三头）
  3. 多任务加权损失训练（学科/题型 LabelSmoothCE，知识点 FocalLoss+LabelSmooth），
     验证集早停
  4. 产物写入 models/versions/<version>/：
     - {subject,type,knowledge}_head.pt（三头权重）
     - manifest.json（版本清单：backbone_ref、标签规模、训练指标）
     微调主干按 manifest.backbone_ref 记录（默认复用共享主干目录，避免每版本复制 400MB 权重）

运行：
  venv\\Scripts\\python scripts\\train_model.py --version v0.2-xxx
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime

import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score, f1_score
from torch.utils.data import DataLoader, Dataset
from transformers import AutoTokenizer, get_linear_schedule_with_warmup

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings, get_settings
from edu_core.inference.model import HierarchicalClassifier

logger = get_logger(__name__)

# 各头损失权重（知识点类别多且难，权重最高）；grade 头启用时改用 GRADE_LOSS_WEIGHTS
LOSS_WEIGHTS = {"subject": 0.28, "question_type": 0.27, "knowledge": 0.45}
GRADE_LOSS_WEIGHTS = {"subject": 0.24, "question_type": 0.23, "knowledge": 0.38, "grade": 0.15}


class FocalLossLabelSmooth(nn.Module):
    """Focal Loss + Label Smoothing：同时处理类别不均衡和过度自信（用于知识点头）。"""

    def __init__(self, alpha: float = 1.0, gamma: float = 1.5, label_smooth: float = 0.08):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.label_smooth = label_smooth

    def forward(self, logits, targets):
        n_cls = logits.size(-1)
        with torch.no_grad():
            smooth_targets = torch.zeros_like(logits).scatter_(
                1, targets.unsqueeze(1), 1.0 - self.label_smooth
            ) + self.label_smooth / n_cls
        log_probs = nn.functional.log_softmax(logits, dim=-1)
        ce_per_sample = -(smooth_targets * log_probs).sum(dim=-1)
        probs = torch.exp(log_probs)
        pt = probs.gather(1, targets.unsqueeze(1)).squeeze(1)
        return (self.alpha * (1.0 - pt) ** self.gamma * ce_per_sample).mean()


class LabelSmoothCE(nn.Module):
    """带 Label Smoothing 的交叉熵（学科/题型头，类别少时平滑效果更好）。"""

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
    """题目分类数据集（text -> 三级标签张量；knowledge/grade 可缺失，缺失记 -100）。"""

    def __init__(self, df: pd.DataFrame, tokenizer, labels: dict, max_len: int):
        self.texts = df["text"].fillna("").tolist()
        self.subjects = [labels["subject2id"][s] for s in df["subject"]]
        self.types = [labels["type2id"][t] for t in df["question_type"]]
        knowledge2id = labels["knowledge2id"]
        self.knowledge = [
            knowledge2id[k] if isinstance(k, str) and k in knowledge2id else -100
            for k in df["knowledge_point"].fillna("")
        ]
        self.grade2id = {g: i for i, g in enumerate(labels.get("grade_bands", []))}
        self.grades = (
            [self.grade2id.get(g, -100) if isinstance(g, str) else -100
             for g in df["grade_band"].fillna("")]
            if self.grade2id else None
        )
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, idx):
        enc = self.tokenizer(
            self.texts[idx], max_length=self.max_len,
            padding="max_length", truncation=True, return_tensors="pt",
        )
        item = {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "subject": torch.tensor(self.subjects[idx], dtype=torch.long),
            "question_type": torch.tensor(self.types[idx], dtype=torch.long),
            "knowledge": torch.tensor(self.knowledge[idx], dtype=torch.long),
        }
        if self.grades is not None:
            item["grade"] = torch.tensor(self.grades[idx], dtype=torch.long)
        return item


def _masked_loss(loss_fn: nn.Module, logits: torch.Tensor, targets: torch.Tensor,
                 device: torch.device) -> torch.Tensor | None:
    """对 -100 缺失标签做掩码损失；整批缺失时返回 None（不参与加权）。"""
    valid = targets.to(device) != -100
    if not bool(valid.any()):
        return None
    return loss_fn(logits.to(device)[valid], targets.to(device)[valid])


def evaluate(model: nn.Module, dataloader: DataLoader, device: torch.device,
             with_subject_ids: bool = False) -> dict:
    """验证/测试：返回各头宏平均 F1 与准确率（-100 缺失标签不计入该头指标）。"""
    model.eval()
    preds: dict[str, list] = {"subject": [], "question_type": [], "knowledge": []}
    trues: dict[str, list] = {"subject": [], "question_type": [], "knowledge": []}
    with torch.no_grad():
        for batch in dataloader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            subject_ids = batch["subject"].to(device) if with_subject_ids else None
            out = model(input_ids, attention_mask, subject_ids=subject_ids)
            heads = [k for k in preds if k in batch]
            if "grade" in batch and "grade" not in preds:
                preds["grade"], trues["grade"] = [], []
                heads.append("grade")
            for k in heads:
                preds[k].extend(out[k].argmax(dim=-1).cpu().tolist())
                trues[k].extend(batch[k].tolist())
    result: dict = {}
    f1_values = []
    for k in preds:
        t = torch.tensor(trues[k])
        p = torch.tensor(preds[k])
        m = t != -100
        if int(m.sum()) == 0:
            continue
        result[f"{k}_acc"] = accuracy_score(t[m], p[m])
        result[f"{k}_f1"] = f1_score(t[m], p[m], average="macro", zero_division=0)
        f1_values.append(result[f"{k}_f1"])
    result["mean_f1"] = sum(f1_values) / len(f1_values) if f1_values else 0.0
    return result


def train(version: str | None = None, settings: Settings | None = None,
          epochs_override: int | None = None) -> dict:
    """完整训练主流程，产物写入 models/versions/<version>/。

    Args:
        version: 版本号；缺省自动生成 v{Y.m.d-HHMMSS}。
        settings: 配置；缺省取全局单例。
        epochs_override: 覆盖配置里的 epoch 数（冒烟训练用）。

    Returns:
        训练摘要 dict（版本号、目录、最佳验证指标）。
    """
    settings = settings or get_settings()
    t_cfg = {"batch_size": 8, "learning_rate": 2.0e-5, "epochs": 12,
             "warmup_ratio": 0.1, "weight_decay": 0.01, "early_stopping_patience": 5}
    if epochs_override:
        t_cfg["epochs"] = epochs_override

    proc_dir = settings.abs_path(settings.data_processed_dir)
    with open(proc_dir / "labels.json", encoding="utf-8") as f:
        labels = json.load(f)
    n_subjects, n_types, n_knowledge = (
        len(labels["subjects"]), len(labels["question_types"]), len(labels["knowledge_points"]))
    logger.info("标签规模：学科 %s / 题型 %s / 知识点 %s", n_subjects, n_types, n_knowledge)

    # 数据追溯链（改进计划 WP-E）：读取数据指纹，写入版本 manifest 的 data_ref
    data_ref: dict | None = None
    data_manifest_path = proc_dir / "data_manifest.json"
    if data_manifest_path.is_file():
        try:
            dm = json.loads(data_manifest_path.read_text(encoding="utf-8"))
            data_ref = {
                "manifest_sha256": hashlib.sha256(
                    data_manifest_path.read_bytes()).hexdigest()[:16],
                "train_sha256": dm.get("files", {}).get("train.csv", {}).get("sha256"),
                "n_train": dm.get("labels_summary", {}).get("n_train"),
                "n_val": dm.get("labels_summary", {}).get("n_val"),
                "n_test": dm.get("labels_summary", {}).get("n_test"),
                "labels_source": dm.get("labels_source"),
                "created_at": dm.get("created_at"),
            }
            logger.info("data_ref 已关联：train sha256 %s…",
                        (data_ref.get("train_sha256") or "")[:16])
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("data_manifest.json 读取失败（训练继续，但版本将缺数据指纹）：%s", exc)
    else:
        logger.warning("缺少 data_manifest.json（scripts/preprocess_all.py 可生成）：%s",
                       data_manifest_path)

    # ---------- 版本目录与主干 ----------
    version = version or f"v0.{datetime.now().month}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    version_dir = settings.abs_path(settings.model_versions_dir) / version
    if version_dir.exists() and any(version_dir.iterdir()):
        raise FileExistsError(f"模型版本目录已存在且非空：{version_dir}")
    version_dir.mkdir(parents=True, exist_ok=True)

    # 微调主干写入版本目录内（改进计划 WP-G2 前置修复）：
    # 旧实现写入共享目录 bert-base-chinese-finetuned，重训会悄悄覆盖旧版本
    # manifest.backbone_ref 指向的权重，破坏版本隔离；现每版本独立存放，
    # 权重不入 git（models/versions/**/*.bin 已在 .gitignore）
    backbone_dir = settings.abs_path(settings.backbone_dir)
    finetuned_dir = version_dir / "backbone"
    finetuned_dir.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(str(backbone_dir))
    logger.info("Tokenizer 加载完成：vocab=%s", len(tokenizer))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("训练设备：%s", device)

    train_df = pd.read_csv(proc_dir / "train.csv")
    val_df = pd.read_csv(proc_dir / "val.csv")
    logger.info("训练 %s / 验证 %s", len(train_df), len(val_df))

    max_len = int(settings.max_seq_length)
    train_loader = DataLoader(QuestionDataset(train_df, tokenizer, labels, max_len),
                              batch_size=t_cfg["batch_size"], shuffle=True, num_workers=0)
    val_loader = DataLoader(QuestionDataset(val_df, tokenizer, labels, max_len),
                            batch_size=t_cfg["batch_size"], shuffle=False, num_workers=0)

    subject_embedding_dim = int(getattr(settings, "subject_embedding_dim", 0) or 0)
    grade_bands = labels.get("grade_bands", [])
    grade_head_enabled = len(grade_bands) > 0
    loss_weights = dict(GRADE_LOSS_WEIGHTS if grade_head_enabled else LOSS_WEIGHTS)
    model = HierarchicalClassifier(
        str(backbone_dir), n_subjects=n_subjects, n_types=n_types,
        n_knowledge=n_knowledge, freeze_ratio=float(settings.freeze_ratio),
        subject_embedding_dim=subject_embedding_dim,
        n_grade_bands=len(grade_bands),
    ).to(device)

    trainable = [p for p in model.parameters() if p.requires_grad]
    n_total = sum(p.numel() for p in model.parameters())
    n_tuned = sum(p.numel() for p in trainable)
    logger.info("参数量：总 %.1fM，可训练 %.1fM（%.1f%%）",
                n_total / 1e6, n_tuned / 1e6, 100 * n_tuned / n_total)

    optimizer = torch.optim.AdamW(trainable, lr=t_cfg["learning_rate"],
                                  weight_decay=t_cfg["weight_decay"])
    total_steps = len(train_loader) * t_cfg["epochs"]
    scheduler = get_linear_schedule_with_warmup(
        optimizer, int(total_steps * t_cfg["warmup_ratio"]), total_steps)

    loss_subject = LabelSmoothCE(label_smooth=0.05)
    loss_type = LabelSmoothCE(label_smooth=0.05)
    loss_knowledge = FocalLossLabelSmooth(alpha=1.0, gamma=1.5, label_smooth=0.08)
    loss_grade = LabelSmoothCE(label_smooth=0.05)

    # ---------- 训练循环（验证集早停） ----------
    patience = int(t_cfg["early_stopping_patience"])
    best_f1 = -1.0
    best_state: dict | None = None
    bad_epochs = 0
    start = time.time()

    for epoch in range(1, t_cfg["epochs"] + 1):
        model.train()
        epoch_loss = 0.0
        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            subject_ids = batch["subject"].to(device)
            out = model(input_ids, attention_mask, subject_ids=subject_ids)
            # 各头掩码损失：缺失标签（-100）不计入该头（新增数据无知识点/学段标注时）
            loss_terms = []
            for head, fn, target in (
                ("subject", loss_subject, batch["subject"]),
                ("question_type", loss_type, batch["question_type"]),
                ("knowledge", loss_knowledge, batch["knowledge"]),
                ("grade", loss_grade, batch.get("grade")),
            ):
                if head not in out or target is None:
                    continue
                term = _masked_loss(fn, out[head], target, device)
                if term is not None:
                    loss_terms.append(loss_weights[head] * term)
            loss = sum(loss_terms)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(trainable, max_norm=1.0)
            optimizer.step()
            scheduler.step()
            epoch_loss += loss.item()
            if (step + 1) % 20 == 0:
                logger.info("epoch %s/%s step %s/%s loss %.4f",
                            epoch, t_cfg["epochs"], step + 1, len(train_loader), epoch_loss / (step + 1))

        val_metric = evaluate(model, val_loader, device, with_subject_ids=subject_embedding_dim > 0)
        mean_f1 = val_metric["mean_f1"]
        logger.info(
            "epoch %s 完成 | 验证 学科 acc %.4f f1 %.4f | 题型 f1 %.4f | 知识点 f1 %.4f | mean_f1 %.4f | 耗时 %.0fs",
            epoch, val_metric["subject_acc"], val_metric["subject_f1"],
            val_metric["question_type_f1"], val_metric["knowledge_f1"], mean_f1, time.time() - start)

        if mean_f1 > best_f1:
            best_f1 = mean_f1
            bad_epochs = 0
            best_state = {
                "bert": copy.deepcopy(model.bert.state_dict()),
                "subject": copy.deepcopy(model.subject_head.state_dict()),
                "type": copy.deepcopy(model.type_head.state_dict()),
                "knowledge": copy.deepcopy(model.knowledge_head.state_dict()),
            }
            if grade_head_enabled:
                best_state["grade"] = copy.deepcopy(model.grade_head.state_dict())
            logger.info("  ↳ 保存最佳模型（mean_f1=%.4f）", best_f1)
        else:
            bad_epochs += 1
            logger.info("  ↳ 验证 F1 未提升（连续 %s/%s）", bad_epochs, patience)
            if bad_epochs >= patience:
                logger.info("早停触发于 epoch %s", epoch)
                break

    # ---------- 保存最佳产物 ----------
    if best_state is None:
        best_state = {
            "bert": model.bert.state_dict(),
            "subject": model.subject_head.state_dict(),
            "type": model.type_head.state_dict(),
            "knowledge": model.knowledge_head.state_dict(),
        }
    torch.save(best_state["bert"], finetuned_dir / "pytorch_model.bin")
    torch.save(model.bert.config, finetuned_dir / "config.pt")
    torch.save(best_state["subject"], version_dir / "subject_head.pt")
    torch.save(best_state["type"], version_dir / "type_head.pt")
    torch.save(best_state["knowledge"], version_dir / "knowledge_head.pt")
    if grade_head_enabled:
        torch.save(best_state["grade"], version_dir / "grade_head.pt")

    manifest = {
        "version": version,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "description": f"多任务微调训练（epochs={t_cfg['epochs']}，best mean_f1={best_f1:.4f}）",
        # manifest 统一记录项目相对路径；predictor/preflight 按项目根解析
        "backbone_ref": str(finetuned_dir.relative_to(settings.root())),        "labels_stats": {
            "n_subjects": n_subjects, "n_types": n_types, "n_knowledge": n_knowledge,
            "n_train": len(train_df), "n_val": len(val_df),
        },
        "train_metrics": {"best_mean_f1": round(float(best_f1), 4)},
        "val_metrics": {k: round(float(v), 4) for k, v in val_metric.items()},
        "loss_weights": loss_weights,
        "data_ref": data_ref,
        "architecture": {
            # predictor 按该声明构建各头（0/false = 旧结构）；改进计划 WP-G2 + 数据扩充轮
            "subject_embedding_dim": subject_embedding_dim,
            "grade_head": grade_head_enabled,
            "grade_bands": grade_bands,
            "max_seq_length": int(settings.max_seq_length),
            "freeze_ratio": float(settings.freeze_ratio),
        },
        "source": "training",
    }
    (version_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("训练完成，最佳 mean_f1=%.4f，版本 %s 已写入 %s", best_f1, version, version_dir)
    return {"version": version, "version_dir": str(version_dir), "best_mean_f1": round(float(best_f1), 4)}


if __name__ == "__main__":
    train()
