"""执行 RAG 基线评测；仅已标注 ready=true 的真实资料用例会调用模型服务。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.config.settings import get_settings
from edu_core.rag.evaluation import acceptance, summarize_runs, validate_cases
from edu_core.rag.generation import RagAnswerService
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from edu_core.storage.stores import StoreBundle

DEFAULT_DATASET = ROOT / "eval_sets" / "rag_baseline.jsonl"
DEFAULT_REPORT = ROOT / "reports" / "verification" / "rag_baseline_latest.json"


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_case(case: dict, service: RagAnswerService) -> dict:
    filters = RetrievalFilters(**{key: value for key, value in (case.get("filters") or {}).items()
                                  if key in {"subject", "grade_band", "grade", "knowledge_node_id"}})
    started = perf_counter()
    debug = service.retrieval.debug(case["query"], role=case.get("role", "student"), filters=filters)
    answer = service.answer(case["query"], role=case.get("role", "student"), filters=filters)
    return {
        "id": case["id"], "category": case["category"], "expected_chunk_ids": case.get("expected_chunk_ids", []),
        "expect_refusal": bool(case.get("expect_refusal")), "candidate_chunk_ids": [item.get("chunk_id") for item in debug["candidates"]],
        "candidate_sources": [item["metadata"]["source_name"] for item in debug["candidates"]],
        "citations": answer.citations, "refused": answer.refused,
        "latency_ms": int((perf_counter() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dry-run", action="store_true", help="仅校验用例，不调用向量库或大模型")
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    errors = validate_cases(cases)
    ready = [case for case in cases if case.get("ready")]
    report = {"dataset": str(args.dataset.relative_to(ROOT)), "total_cases": len(cases), "ready_cases": len(ready),
              "validation_errors": errors, "status": "ready"}
    if errors:
        report["status"] = "invalid"
    elif not ready:
        report["status"] = "not_ready"
        report["reason"] = "尚无已关联真实资料分块并复核过的 ready=true 用例，未发起模型调用。"
    elif args.dry_run:
        report["status"] = "dry_run"
    else:
        settings, stores = get_settings(), StoreBundle()
        service = RagAnswerService(RagRetrievalService(stores.rag, settings), settings)
        runs = [run_case(case, service) for case in ready]
        report["metrics"] = summarize_runs(runs)
        report["acceptance"] = acceptance(report["metrics"])
        report["runs"] = runs
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "total_cases", "ready_cases", "validation_errors")}, ensure_ascii=False))
    return 0 if report["status"] in {"ready", "not_ready", "dry_run"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
