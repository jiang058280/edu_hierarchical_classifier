"""R2.5：可复跑、可审计的 RAG 基线评测指标。"""

from __future__ import annotations

import math
from statistics import quantiles
from typing import Iterable


REQUIRED_CATEGORIES = {"answerable", "unanswerable", "cross_grade", "wrong_question", "follow_up"}


def validate_cases(cases: list[dict], *, minimum: int = 100) -> list[str]:
    errors: list[str] = []
    if len(cases) < minimum:
        errors.append(f"评测集至少需要 {minimum} 条，当前仅 {len(cases)} 条")
    categories = {case.get("category") for case in cases}
    missing = REQUIRED_CATEGORIES - categories
    if missing:
        errors.append(f"缺少场景：{', '.join(sorted(missing))}")
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("评测用例 id 必须唯一")
    for case in cases:
        if not str(case.get("query") or "").strip():
            errors.append(f"{case.get('id', '未知')} 缺少 query")
    return errors


def _ranking_scores(expected: set[int], ranked: list[int]) -> tuple[float | None, float | None, float | None]:
    if not expected:
        return None, None, None
    hits = [index for index, item in enumerate(ranked, start=1) if item in expected]
    recall = len(set(ranked) & expected) / len(expected)
    mrr = 1 / hits[0] if hits else 0.0
    dcg = sum(1 / math.log2(index + 1) for index in hits)
    ideal = sum(1 / math.log2(index + 1) for index in range(1, min(len(expected), len(ranked)) + 1))
    return recall, mrr, dcg / ideal if ideal else 0.0


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(sum(values) / len(values), 4) if values else None


def summarize_runs(runs: list[dict]) -> dict:
    """聚合线上运行记录；无人工标注的忠实度只报告证据约束代理指标。"""
    recalls, mrrs, ndcgs, latencies = [], [], [], []
    citation_checks, refusal_checks, source_free, faithful = [], [], [], []
    for run in runs:
        expected = ({int(item) for item in run.get("expected_chunk_ids", [])}
                    if run.get("route") != "local_question" else set())
        ranked = [int(item) for item in run.get("candidate_chunk_ids", []) if item is not None]
        recall, mrr, ndcg = _ranking_scores(expected, ranked)
        if recall is not None:
            recalls.append(recall)
            mrrs.append(mrr)
            ndcgs.append(ndcg)
        citations = run.get("citations", [])
        cited_sources = {item.get("source_name") for item in citations}
        candidate_sources = set(run.get("candidate_sources", []))
        if run.get("refused") is False:
            citation_checks.append(bool(citations) and cited_sources <= candidate_sources)
            source_free.append(not citations)
            # 不是 LLM 裁判，只验证“生成回答必有服务端可验证引用”的强约束。
            faithful.append(bool(citations) and cited_sources <= candidate_sources)
        if "expect_refusal" in run:
            refusal_checks.append(bool(run["refused"]) == bool(run["expect_refusal"]))
        if run.get("latency_ms") is not None:
            latencies.append(float(run["latency_ms"]))
    p95 = None
    if latencies:
        p95 = round(max(latencies) if len(latencies) == 1 else quantiles(latencies, n=100, method="inclusive")[94], 2)
    return {
        "case_count": len(runs), "retrieval_evaluated_count": len(recalls),
        "local_question_count": sum(run.get("route") == "local_question" for run in runs),
        "rag_count": sum(run.get("route") == "rag" for run in runs),
        "recall_at_k": _mean(recalls), "mrr": _mean(mrrs), "ndcg": _mean(ndcgs),
        "citation_validity": _mean(float(item) for item in citation_checks),
        "faithfulness_evidence_proxy": _mean(float(item) for item in faithful),
        "refusal_accuracy": _mean(float(item) for item in refusal_checks),
        "no_source_answer_rate": _mean(float(item) for item in source_free), "p95_latency_ms": p95,
    }


def acceptance(metrics: dict) -> dict:
    thresholds = {"recall_at_k": 0.85, "citation_validity": 0.95,
                  "no_source_answer_rate": 0.0, "refusal_accuracy": 0.90}
    checks = {}
    for key, threshold in thresholds.items():
        value = metrics.get(key)
        checks[key] = value is not None and value <= threshold if key == "no_source_answer_rate" else value is not None and value >= threshold
    return {"thresholds": thresholds, "checks": checks, "passed": all(checks.values())}
