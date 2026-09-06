"""知识点树播种：把 labels.json 的 50 个 学科::一级知识点 导入 knowledge_nodes（平台 M0）。

- 层级结构：level=1（一级知识点），parent_id=NULL，grade_band=NULL（初高中通用）；
- 幂等：UNIQUE KEY (subject, grade_band, name, level) + INSERT IGNORE，重复执行无副作用；
- 后续细化（学段拆分/章节级）由教师在平台标注积累后追加 level=2 节点。

运行：venv\\Scripts\\python scripts\\seed_knowledge_nodes.py
"""

from __future__ import annotations

import json
import sys as _sys
from pathlib import Path as _Path

for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

from edu_core.config.settings import get_settings
from edu_core.storage.stores import get_engine

from sqlalchemy import text


def main() -> None:
    settings = get_settings()
    labels_path = settings.abs_path(settings.data_processed_dir) / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    engine = get_engine(settings)

    inserted = skipped = 0
    with engine.begin() as conn:
        for kp in labels["knowledge_points"]:
            subject, _, name = kp.partition("::")
            if not name:
                continue
            result = conn.execute(text(
                "INSERT IGNORE INTO knowledge_nodes (subject, grade_band, name, level) "
                "VALUES (:s, NULL, :n, 1)"
            ), {"s": subject, "n": name})
            inserted += result.rowcount
            skipped += 1 - result.rowcount

    total = 0
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM knowledge_nodes")).scalar_one()
    print(f"播种完成：新增 {inserted} 个节点，跳过重复 {skipped} 个；知识树现有 {total} 个节点")


if __name__ == "__main__":
    main()
