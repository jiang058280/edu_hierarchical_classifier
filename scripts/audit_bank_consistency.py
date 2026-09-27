# -*- coding: utf-8 -*-
"""题库一致性审计：用 active 模型对全部已发布/草稿题重预测，标记与人工标注不一致的题目。

- 对比维度：学科（模型 acc 0.987）、题型（三分类粗口径，F1 0.94）、学段（初/高）
- 知识点不参与对比：模型为 50 粗类，题库为 354 细粒度，粒度不同不可直接比
- 产出 JSON 报告；不一致项供教师在题库中复核修正（模型也可能错，标注优先、模型佐证）
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.application.factory import get_classification_service
from edu_core.application.question_taxonomy import TYPE_META
from sqlalchemy import text

OUT = ROOT / "reports" / "verification" / "bank_consistency_audit_20260927.json"


def main() -> int:
    service = get_classification_service()
    predictor = service.predictor
    stores = service.stores
    with stores.questions.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, subject, question_type, knowledge_point, grade_band, grade,
                   status, LEFT(content, 80) AS head
            FROM questions WHERE status IN ('published', 'draft') ORDER BY id""")).mappings().all()
    print(f"待审计题目 {len(rows)} 道")

    items, flag_counter = [], Counter()
    for row in rows:
        question = dict(row)
        prediction = predictor.predict(question.pop("head"))
        flags = []
        subject_pred = prediction.get("subject") or ""
        if subject_pred and subject_pred != question["subject"]:
            flags.append("subject_mismatch")
        coarse = TYPE_META.get(question["question_type"] or "", (None, None, question["question_type"] or ""))[2]
        type_pred = prediction.get("question_type") or ""
        if type_pred and coarse and type_pred != coarse:
            flags.append("type_mismatch")
        band_label = question.get("grade_band") or ""
        band_pred = prediction.get("grade_band") or ""
        if band_label and band_pred and band_pred != band_label:
            flags.append("grade_band_mismatch")
        for flag in flags:
            flag_counter[flag] += 1
        items.append({
            "id": question["id"], "status": question["status"],
            "subject": question["subject"], "subject_pred": subject_pred,
            "question_type": question["question_type"], "type_coarse": coarse, "type_pred": type_pred,
            "grade_band": band_label, "band_pred": band_pred,
            "knowledge_point": question["knowledge_point"],
            "confidence": prediction.get("confidence", {}),
            "flags": flags,
        })

    flagged = [item for item in items if item["flags"]]
    summary = {
        "total": len(items),
        "clean": len(items) - len(flagged),
        "flags": dict(flag_counter),
        "subject_mismatch_samples": [
            {"id": item["id"], "label": item["subject"], "pred": item["subject_pred"],
             "kp": item["knowledge_point"][:24], "status": item["status"]}
            for item in flagged if "subject_mismatch" in item["flags"]][:40],
    }
    report = {"generated_at": "2026-09-27", "model_version": predictor.model_version,
              "summary": summary, "flagged": flagged,
              "note": "模型预测仅作佐证（模型也可能错）；修正请走题库编辑，编辑会同步审计与向量。"}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("报告已写出:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
