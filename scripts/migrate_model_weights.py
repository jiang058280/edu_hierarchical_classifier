"""把 v0.1 现有权重注册为首个受治理的模型版本 v0.1-base。

一次性迁移脚本：
    models/heads/{subject,type,knowledge}_head.pt
        -> models/versions/v0.1-base/ 三头 + manifest.json（backbone_ref 指向现有微调主干）

用法：
    venv\\Scripts\\python scripts\\migrate_model_weights.py
    # 注册 + 激活（需 MySQL 已初始化）：
    venv\\Scripts\\python scripts\\rebuild_model_version.py --version v0.1-base --skip-evaluation --activate
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import get_settings

VERSION = "v0.1-base"
HEADS = ["subject_head.pt", "type_head.pt", "knowledge_head.pt"]


def main() -> None:
    settings = get_settings()
    heads_dir = settings.root("models", "heads")
    missing = [h for h in HEADS if not (heads_dir / h).is_file()]
    if missing:
        raise SystemExit(f"旧权重缺失：{heads_dir} 缺少 {missing}，无法迁移")

    backbone_ref = "models/pretrained_backbone/bert-base-chinese-finetuned"
    if not (settings.root(backbone_ref) / "pytorch_model.bin").is_file():
        raise SystemExit(f"微调主干权重缺失：{settings.root(backbone_ref)}")

    vdir = settings.abs_path(settings.model_versions_dir) / VERSION
    vdir.mkdir(parents=True, exist_ok=True)
    for h in HEADS:
        shutil.copy2(heads_dir / h, vdir / h)

    manifest = {
        "version": VERSION,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "description": "旧版 models/heads 权重迁移（v0.1-demo 基线：subject acc 97.2% / type F1 91.9% / knowledge F1 55.5%）",
        "backbone_ref": backbone_ref,
        "labels_stats": {},
        "source": "legacy-migrate",
    }
    (vdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已迁移旧权重到版本目录：{vdir}")
    print("下一步（需先 scripts/init_db.py）：")
    print(f"  venv\\Scripts\\python scripts\\rebuild_model_version.py --version {VERSION} "
          f"--limit 50 --gate --activate")


if __name__ == "__main__":
    main()
