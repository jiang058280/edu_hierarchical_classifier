"""评测使用回答实际经过的单次检索，不伪装多轮回放。"""

import pytest
import json
import sys

from edu_core.config.settings import Settings
from edu_core.rag.evaluation import summarize_runs
from edu_core.rag.generation import RagAnswerService
from scripts.evaluate_rag import execution_errors, run_case
from scripts import evaluate_rag


class Retrieval:
    def __init__(self, local=False):
        self.calls = 0
        self.local = local

    def local_question_candidates(self, *args, **kwargs):
        return ([{"question_id": 9, "question_text": "题目", "answer": "答案", "score": 1.0}]
                if self.local else [])

    def debug(self, *args, **kwargs):
        self.calls += 1
        assert self.calls == 1, "回答不能重复检索"
        return {"normalized_query": "问题", "candidates": [{
            "chunk_id": 8, "score": 1.0, "content": "资料内容",
            "metadata": {"source_name": "讲义", "kb_version": "v1"},
        }]}


class Chat:
    def complete(self, messages):
        return "根据资料的回答"


def case(**kwargs):
    return {"id": "one", "query": "问题", "category": "answerable",
            "expected_chunk_ids": [8], "expect_refusal": False, **kwargs}


def test_evaluation_reuses_actual_retrieval_and_retains_answer():
    retrieval = Retrieval()
    service = RagAnswerService(retrieval, Settings(_env_file=None), Chat())
    result = run_case(case(), service)
    assert retrieval.calls == 1
    assert result["candidate_chunk_ids"] == [8]
    assert result["route"] == "rag"
    assert result["answer"] == "根据资料的回答"
    assert summarize_runs([result])["recall_at_k"] == 1.0


def test_local_route_does_not_retrieve_or_pollute_rag_metrics():
    retrieval = Retrieval(local=True)
    service = RagAnswerService(retrieval, Settings(_env_file=None), Chat())
    result = run_case(case(), service)
    metrics = summarize_runs([result])
    assert retrieval.calls == 0
    assert result["route"] == "local_question"
    assert metrics["local_question_count"] == 1
    assert metrics["retrieval_evaluated_count"] == 0
    assert metrics["recall_at_k"] is None
    assert metrics["citation_validity"] == 1.0


@pytest.mark.parametrize("extra", [
    {"category": "follow_up"},
    {"conversation_history": [{"role": "user", "content": "上一轮"}]},
])
def test_multiturn_rejected_before_any_service_call(extra):
    assert execution_errors([case(**extra)])
    with pytest.raises(ValueError, match="会话回放"):
        run_case(case(**extra), None)


def test_draft_history_is_not_silently_treated_as_single_turn():
    assert not execution_errors([case()])
    assert execution_errors([case(category="follow_up", ready=False)])


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_preflights_all_ready_cases_before_constructing_services(tmp_path, monkeypatch, dry_run):
    dataset, output = tmp_path / "cases.jsonl", tmp_path / "report.json"
    categories = ["answerable", "unanswerable", "cross_grade", "wrong_question", "follow_up"]
    dataset.write_text("\n".join(json.dumps(case(id=str(i), category=categories[i % 5], ready=True))
                                 for i in range(100)), encoding="utf-8")
    def forbidden():
        pytest.fail("不支持的评测不能构造数据库或模型服务")
    monkeypatch.setattr(evaluate_rag, "StoreBundle", forbidden)
    monkeypatch.setattr(evaluate_rag, "get_settings", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate_rag", "--dataset", str(dataset), "--output", str(output)]
                        + (["--dry-run"] if dry_run else []))
    assert evaluate_rag.main() == (0 if dry_run else 2)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == ("dry_run" if dry_run else "unsupported")
    assert len(report["execution_errors"]) == 20
    assert "metrics" not in report
