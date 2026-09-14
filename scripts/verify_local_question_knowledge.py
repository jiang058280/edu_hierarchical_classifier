"""本地题库知识源验收：只读 MySQL，不调用 Embedding、Milvus 或聊天模型。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edu_core.config.settings import get_settings
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from edu_core.storage.stores import StoreBundle


def main() -> int:
    settings = get_settings()
    stores = StoreBundle(settings=settings)
    rows = stores.rag.list_local_question_knowledge()
    sample = rows[:100]
    if not sample:
        raise SystemExit("本地题库知识源为空；请先在知识库管理页执行同步")
    service = RagRetrievalService(stores.rag, settings)
    matched, answer_ready = 0, 0
    failures = []
    for item in sample:
        hits = service.local_question_candidates(
            item["question_text"],
            filters=RetrievalFilters(subject=item.get("subject"), grade_band=item.get("grade_band"),
                                     grade=item.get("grade")),
        )
        if hits and int(hits[0]["question_id"]) == int(item["question_id"]):
            matched += 1
        else:
            failures.append(int(item["question_id"]))
        if (item.get("answer") or "").strip() or (item.get("analysis") or "").strip():
            answer_ready += 1
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "MySQL rag_question_knowledge",
        "external_model_called": False,
        "total_synced": len(rows), "sample_size": len(sample), "top1_matched": matched,
        "top1_recall": round(matched / len(sample), 4), "answer_or_analysis_ready": answer_ready,
        "failed_question_ids": failures,
        "note": "这是本地题库精确命中验收，不替代 R2.5 的人工标注资料问答评测。",
    }
    target = PROJECT_ROOT / "reports/verification/local_question_knowledge_latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if matched == len(sample) else 1


if __name__ == "__main__":
    raise SystemExit(main())
