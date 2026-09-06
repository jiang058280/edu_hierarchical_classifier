"""数据清单（data manifest）：为 data/processed 产物建立可追溯指纹（改进计划 WP-E）。

功能：
  1. 计算 train/val/test.csv 与 labels.json 的 SHA256 + 行数；
  2. 读取 labels.json 的划分统计与清洗规则说明；
  3. 记录 git commit、生成时间与数据来源（DATA_PROVENANCE.md 承接叙事）；
  4. 输出 data/processed/data_manifest.json —— 训练时 train.py 会把其中的
     指纹写入模型版本 manifest 的 data_ref 字段，形成"版本 ↔ 数据"追溯链。

关于增强产物：当前 CSV 为 v0.1 阶段 legacy/src/augment_data.py 的一次性增强产物，
增强超参数未留存、精确复现不可得（详见 DATA_PROVENANCE.md 第三节）。
因此本脚本默认**只登记现状**（--manifest-only），绝不重写 CSV；
重跑"清洗 + 增强"全量管道的功能随增强脚本参数化后再开放。

运行：
    venv\\Scripts\\python scripts\\preprocess_all.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys as _sys
from datetime import datetime
from pathlib import Path
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings

logger = get_logger(__name__)

MANIFEST_NAME = "data_manifest.json"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_row_count(path: Path) -> int:
    import csv
    with open(path, encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=str(root))
        return out.stdout.strip() or None
    except OSError:
        return None


def labels_source_stats(proc_dir: Path) -> dict | None:
    """若 CSV 带 labels_source 列（WP-F），统计 rule/manual 分布。"""
    import csv
    train_csv = proc_dir / "train.csv"
    if not train_csv.is_file():
        return None
    with open(train_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if "labels_source" not in (reader.fieldnames or []):
            return None
        counts = {"rule": 0, "manual": 0}
        for row in reader:
            key = row.get("labels_source") or "rule"
            counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values()) or 1
    counts["manual_ratio"] = round(counts.get("manual", 0) / total, 4)
    return counts


def build_manifest(settings) -> dict:
    root = settings.root()
    proc_dir = settings.abs_path(settings.data_processed_dir)
    files = {}
    for name in ("train.csv", "val.csv", "test.csv", "labels.json"):
        fp = proc_dir / name
        if not fp.is_file():
            raise FileNotFoundError(f"数据产物缺失，请先完成数据预处理：{fp}")
        entry = {"sha256": sha256_of(fp), "bytes": fp.stat().st_size}
        if name.endswith(".csv"):
            entry["rows"] = csv_row_count(fp)
        files[name] = entry

    labels = json.loads((proc_dir / "labels.json").read_text(encoding="utf-8"))
    stats = labels.get("stats", {})
    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": git_commit(root),
        "files": files,
        "labels_summary": {
            "n_samples": stats.get("n_samples"),
            "n_train": stats.get("n_train"), "n_val": stats.get("n_val"),
            "n_test": stats.get("n_test"),
            "n_subjects": len(labels.get("subjects", [])),
            "n_types": len(labels.get("question_types", [])),
            "n_knowledge": len(labels.get("knowledge_points", [])),
            "split_rule": "按学科分层 70/15/15（seed=42）",
        },
        "labels_source": labels_source_stats(proc_dir),
        "provenance": {
            "raw_source": "K-12EduBench（data/raw/k12edubench，9 学科 JSON）",
            "pipeline": "edu_core.data.dataset（清洗/去重/小类合并/分层划分）",
            "augment": "v0.1 阶段 legacy/src/augment_data.py 一次性增强产物，"
                       "超参数未留存、精确复现不可得（登记现状，详见 DATA_PROVENANCE.md）",
            "doc": "DATA_PROVENANCE.md",
        },
    }
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 data_manifest.json（数据指纹登记）")
    parser.add_argument("--manifest-only", action="store_true", default=True,
                        help="只对当前 CSV/labels 生成指纹，不重跑预处理（当前唯一模式）")
    parser.parse_args()

    settings = get_settings()
    manifest = build_manifest(settings)
    out = settings.abs_path(settings.data_processed_dir) / MANIFEST_NAME
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    fs = manifest["files"]
    print(f"data manifest 已写入：{out}")
    print(f"  train/val/test = {fs['train.csv']['rows']}/{fs['val.csv']['rows']}/{fs['test.csv']['rows']} 行")
    print(f"  labels.source 分布：{manifest['labels_source'] or '（无 labels_source 列）'}")
    print(f"  train.csv sha256：{fs['train.csv']['sha256'][:16]}…")


if __name__ == "__main__":
    main()
