# -*- coding: utf-8 -*-
"""v0.5 数据增补：将题库已发布题（教师标注）并入训练集。

- 仅 published（AI 草稿未审核，绝不入训练集）
- question_type 经题型目录映射到三分类粗口径；knowledge_point 为细粒度标签，
  不在 50 类词表内会被训练管线自动掩码（-100），保留原值作数据溯源
- 与现有 train 文本指纹去重（normalized 精确匹配）；与 golden 300 不同源
- 先备份 train.csv，再追加；统计入 data_manifest（preprocess_all.py 另行刷新）
"""
import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edu_core.application.question_taxonomy import TYPE_META  # noqa: E402
from edu_core.storage.stores import StoreBundle  # noqa: E402

TRAIN = Path("data/processed/train.csv")
BACKUP = Path("data/processed/train.pre_v05_backup.csv")


def normalized(text: str) -> str:
    return "".join(str(text).split())


def main() -> int:
    if not TRAIN.exists():
        raise SystemExit("train.csv 不存在")
    train = pd.read_csv(TRAIN)
    existing = {normalized(text) for text in train["text"].fillna("")}
    print(f"现有训练集 {len(train)} 行")

    stores = StoreBundle()
    with stores.questions.engine.connect() as conn:
        from sqlalchemy import text
        rows = conn.execute(text("""
            SELECT id, content, subject, question_type, knowledge_point,
                   answer, analysis, difficulty, grade_band, grade
            FROM questions WHERE status = 'published' ORDER BY id""")).mappings().all()

    added, skipped_dup, skipped_type, skipped_overlap = [], 0, 0, 0
    for row in rows:
        fine_type = (row["question_type"] or "").strip()
        meta = TYPE_META.get(fine_type)
        if not meta:
            skipped_type += 1
            continue
        coarse = meta[2]
        content = str(row["content"] or "").strip()
        fingerprint = normalized(content)
        if not content or fingerprint in existing:
            skipped_overlap += 1
            continue
        existing.add(fingerprint)
        added.append({
            "text": content,
            "subject": row["subject"],
            "question_type": coarse,
            "knowledge_point": (row["knowledge_point"] or "").strip(),
            "raw_question_type": fine_type,
            "answer": str(row["answer"] or ""),
            "labels_source": "platform_bank",
            "grade_band": row["grade_band"] or "",
            "source": "platform_bank",
            "oversampled": 0,
        })

    if not added:
        print("无新增行（可能已增补过），退出")
        return 0
    BACKUP.write_bytes(TRAIN.read_bytes())
    augmented = pd.concat([train, pd.DataFrame(added)], ignore_index=True)
    augmented.to_csv(TRAIN, index=False)
    by_subject: dict[str, int] = {}
    for item in added:
        by_subject[item["subject"]] = by_subject.get(item["subject"], 0) + 1
    print(f"已备份 → {BACKUP}")
    print(f"新增 {len(added)} 行（跳过：库内重叠 {skipped_overlap}、无粗映射题型 {skipped_type}）")
    print("新增学科分布:", dict(sorted(by_subject.items(), key=lambda kv: -kv[1])))
    print(f"训练集 {len(train)} → {len(augmented)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
