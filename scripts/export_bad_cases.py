"""Bad Case 导出（对齐 knowforge 的 extract_bad_cases_from_report.py）。

从 MySQL 反馈/分类留痕中筛选低置信 + 判错样本，导出到 eval_sets/bad_cases.json，
人工复核修正标签后可并入再训练数据（反馈闭环的最后一公里）。

用法：
    venv\\Scripts\\python scripts\\export_bad_cases.py --output eval_sets/bad_cases.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import get_settings
from edu_core.storage.stores import StoreBundle


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 Bad Case 候选集")
    parser.add_argument("--output", default="eval_sets/bad_cases.json")
    parser.add_argument("--limit", type=int, default=100, help="最多导出条数")
    args = parser.parse_args()

    settings = get_settings()
    stores = StoreBundle(settings=settings)

    # 候选来源 1：判错反馈关联的分类记录（最优质）
    bad_cases: list[dict] = []
    seen_ids: set[int] = set()
    for fb in stores.feedback.list(limit=200, is_correct=False):
        if fb["classification_id"] and fb["classification_id"] not in seen_ids:
            record = stores.classifications.get(fb["classification_id"])
            if record:
                bad_cases.append({
                    "source": "feedback_wrong",
                    "classification_id": record["id"],
                    "model_version": record["model_version"],
                    "label_source": "model_prediction",
                    "text": record["text_preview"],
                    "predicted": {
                        "subject": record["subject_pred"],
                        "question_type": record["type_pred"],
                        "knowledge_point": record["knowledge_pred"],
                    },
                    "corrected": {
                        "subject": fb["corrected_subject"],
                        "question_type": fb["corrected_type"],
                        "knowledge_point": fb["corrected_knowledge"],
                    },
                    "avg_confidence": float(record["avg_confidence"]),
                    "created_at": str(record["created_at"]),
                    "review_status": "pending",
                })
                seen_ids.add(fb["classification_id"])

    # 候选来源 2：低置信度分类（band=low，无反馈）——主动学习候选
    for record in stores.classifications.recent(limit=500):
        if record["confidence_band"] != "low" or record["id"] in seen_ids:
            continue
        bad_cases.append({
            "source": "low_confidence",
            "classification_id": record["id"],
            "model_version": record["model_version"],
            "label_source": "model_prediction",
            "text": record["text_preview"],
            "predicted": {
                "subject": record["subject_pred"],
                "question_type": record["type_pred"],
                "knowledge_point": record["knowledge_pred"],
            },
            "corrected": None,
            "avg_confidence": float(record["avg_confidence"]),
            "created_at": str(record["created_at"]),
            "review_status": "pending",
        })
        if len(bad_cases) >= args.limit:
            break

    out = Path(args.output)
    if not out.is_absolute():
        out = get_root() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {"low_confidence": settings.badcase_low_confidence},
        "count": len(bad_cases),
        "cases": bad_cases,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Bad Case 候选集已导出：{out}（{len(bad_cases)} 条；"
          "人工复核 review_status 与 corrected 字段后并入再训练数据）")


if __name__ == "__main__":
    main()
