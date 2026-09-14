from __future__ import annotations

from edu_core.rag.evaluation import acceptance, summarize_runs, validate_cases


def test_metrics_include_ranking_citation_refusal_and_latency():
    metrics = summarize_runs([{
        "expected_chunk_ids": [8], "candidate_chunk_ids": [8, 9], "candidate_sources": ["讲义"],
        "citations": [{"source_name": "讲义"}], "refused": False, "expect_refusal": False, "latency_ms": 88,
    }, {
        "expected_chunk_ids": [], "candidate_chunk_ids": [], "candidate_sources": [],
        "citations": [], "refused": True, "expect_refusal": True, "latency_ms": 112,
    }])
    assert metrics["recall_at_k"] == 1.0 and metrics["mrr"] == 1.0 and metrics["ndcg"] == 1.0
    assert metrics["citation_validity"] == 1.0 and metrics["refusal_accuracy"] == 1.0
    assert metrics["no_source_answer_rate"] == 0.0 and metrics["p95_latency_ms"] >= 88


def test_acceptance_requires_all_baseline_thresholds():
    report = acceptance({"recall_at_k": 0.9, "citation_validity": 1.0,
                         "no_source_answer_rate": 0.0, "refusal_accuracy": 0.91})
    assert report["passed"] is True
    assert acceptance({"recall_at_k": 0.9, "citation_validity": 1.0,
                       "no_source_answer_rate": 0.01, "refusal_accuracy": 0.91})["passed"] is False


def test_validation_requires_100_cases_and_all_scenarios():
    assert validate_cases([{"id": "one", "category": "answerable", "query": "x"}])
