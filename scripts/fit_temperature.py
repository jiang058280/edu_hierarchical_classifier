"""置信度温度校准（temperature scaling，改进计划 WP-G3）。

在验证集上为指定模型版本拟合单一全局温度 T（三个头共用），
最小化正确类负对数似然（NLL）。拟合结果写入版本 manifest 的 temperature 字段，
predictor 推理时按 logits/T 再 softmax —— 只改变置信度分布，不改变 argmax 标签，
因此不影响准确率类指标，仅让置信度分级（high/medium/low 复核建议）变得可信。

报告输出 reports/evaluation/<version>_calibration.json：
NLL / ECE（10 桶）校准前后对比。

用法：
    venv\\Scripts\\python scripts\\fit_temperature.py --version v0.2-xxx
"""

from __future__ import annotations

import argparse
import json
import sys as _sys
from datetime import datetime
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

import pandas as pd
import torch
import torch.nn.functional as F

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings
from edu_core.inference.predictor import HierarchicalPredictor
from edu_core.training.train import QuestionDataset
from torch.utils.data import DataLoader

logger = get_logger(__name__)

HEADS = ("subject", "question_type", "knowledge")


def collect_logits(predictor: HierarchicalPredictor, csv_path, settings) -> tuple[dict, dict]:
    """收集验证集上三个头的原始 logits 与真实标签（都在 CPU）。"""
    labels = predictor.labels
    tokenizer, max_len = predictor.tokenizer, predictor.max_len
    df = pd.read_csv(csv_path)
    loader = DataLoader(QuestionDataset(df, tokenizer, labels, max_len),
                        batch_size=16, shuffle=False, num_workers=0)
    model = predictor.model  # 已 eval + to(device)；quantized 在 CPU 场景亦可前向
    logits = {k: [] for k in HEADS}
    targets = {k: [] for k in HEADS}
    with torch.no_grad():
        for batch in loader:
            input_ids = batch["input_ids"].to(predictor.device)
            attention_mask = batch["attention_mask"].to(predictor.device)
            subject_ids = batch["subject"].to(predictor.device) \
                if predictor.subject_embedding_dim > 0 else None
            out = model(input_ids, attention_mask, subject_ids=subject_ids)
            for k in HEADS:
                logits[k].append(out[k].float().cpu())
                targets[k].append(batch[k])
    return ({k: torch.cat(v) for k, v in logits.items()},
            {k: torch.cat(v) for k, v in targets.items()})


def nll_and_ece(logits: torch.Tensor, targets: torch.Tensor,
                temperature: float, n_bins: int = 10) -> dict:
    scaled = logits / temperature
    log_probs = F.log_softmax(scaled, dim=-1)
    nll = F.nll_loss(log_probs, targets).item()
    probs = log_probs.exp()
    conf, pred = probs.max(dim=-1)
    acc = (pred == targets).float()
    ece, bucket = 0.0, 0.0
    edges = torch.linspace(0, 1, n_bins + 1)
    for i in range(n_bins):
        in_bin = (conf > edges[i]) & (conf <= edges[i + 1])
        if in_bin.any():
            gap = (acc[in_bin].mean() - conf[in_bin].mean()).abs().item()
            ece += in_bin.float().mean().item() * gap
            bucket += 1
    return {"nll": round(nll, 4), "ece": round(ece, 4),
            "acc": round(acc.mean().item(), 4), "bins_used": bucket}


def main() -> None:
    parser = argparse.ArgumentParser(description="验证集拟合置信度温度并写入版本 manifest")
    parser.add_argument("--version", required=True, help="版本号（models/versions/<version>）")
    parser.add_argument("--split", default="val.csv", choices=["val.csv", "train.csv"])
    parser.add_argument("--max-rows", type=int, default=2000, help="拟合所用最大样本数")
    args = parser.parse_args()

    settings = get_settings()
    version_dir = settings.abs_path(settings.model_versions_dir) / args.version
    predictor = HierarchicalPredictor(version_dir, settings=settings, verbose=False)

    csv_path = settings.abs_path(settings.data_processed_dir) / args.split
    df = pd.read_csv(csv_path)
    if args.max_rows and len(df) > args.max_rows:
        df = df.sample(n=args.max_rows, random_state=42)
    tmp = csv_path.with_suffix(".calib_sample.csv")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        logits, targets = collect_logits(predictor, tmp, settings)
    finally:
        tmp.unlink(missing_ok=True)

    # 拟合单一全局 T（LBFGS，标量凸优化）
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=50)

    def closure():
        opt.zero_grad()
        t = log_t.exp().clamp(0.2, 5.0)
        loss = sum(F.cross_entropy(logits[k] / t, targets[k]) for k in HEADS)
        loss.backward()
        return loss

    opt.step(closure)
    temperature = float(log_t.exp().clamp(0.2, 5.0).item())

    before = {k: nll_and_ece(logits[k], targets[k], 1.0) for k in HEADS}
    after = {k: nll_and_ece(logits[k], targets[k], temperature) for k in HEADS}

    report = {
        "version": args.version,
        "split": args.split,
        "n_samples": int(len(targets["subject"])),
        "temperature": round(temperature, 4),
        "before": before,
        "after": after,
        "fitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 写入 manifest（predictor 加载时读取）
    manifest_path = version_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["temperature"] = round(temperature, 4)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    out = settings.abs_path(settings.reports_dir) / "evaluation" / f"{args.version}_calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"拟合温度 T={temperature:.4f}（基于 {args.split} {report['n_samples']} 条）")
    for k in HEADS:
        b, a = before[k], after[k]
        print(f"  {k:14s} NLL {b['nll']:.4f} -> {a['nll']:.4f} | ECE {b['ece']:.4f} -> {a['ece']:.4f}")
    print(f"校准报告：{out}（已写入 manifest.temperature，推理端自动生效）")


if __name__ == "__main__":
    main()
