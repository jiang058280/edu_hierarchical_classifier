"""训练入口：调用 edu_core.training.train 完成多任务微调，产物写入版本目录。

用法：
    venv\\Scripts\\python scripts\\train_model.py --version v0.2-20260905
    venv\\Scripts\\python scripts\\train_model.py --epochs 1   # 冒烟训练
"""

from __future__ import annotations

import argparse

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.training.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description="多任务微调训练")
    parser.add_argument("--version", default=None, help="版本号（缺省自动生成）")
    parser.add_argument("--epochs", type=int, default=None, help="覆盖配置 epoch 数（冒烟训练用）")
    args = parser.parse_args()
    summary = train(version=args.version, epochs_override=args.epochs)
    print("训练完成：", summary)
    print("下一步：python scripts/rebuild_model_version.py "
          f"--version {summary['version']} --gate --activate")


if __name__ == "__main__":
    main()
