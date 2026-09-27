from __future__ import annotations

import pytest

from edu_core.rag.conversations import RagConversationService
from edu_core.api.student import _answer_stream_chunks
from edu_core.rag.generation import RagAnswer
from edu_core.rag.retrieval import RetrievalFilters


class _Store:
    def __init__(self):
        self.created = []
        self.messages = []
        self.traces = []

    def create_session(self, user_id, role, title):
        self.created.append((user_id, role, title))
        return 19

    def get_owned_session(self, session_id, user_id, role):
        return {"id": session_id} if (session_id, user_id, role) == (19, 7, "student") else None

    def add_message(self, session_id, role, content, **kwargs):
        self.messages.append((session_id, role, content, kwargs))
        return len(self.messages) + 100

    def add_query_trace(self, **kwargs):
        self.traces.append(kwargs)
        return 1


class _AnswerService:
    def answer(self, query, *, role, filters):
        assert role == "student"
        assert filters.grade_band == "初中"
        return RagAnswer("依据资料的答案", [{"source_name": "教材", "score": 0.91, "kb_version": "v1"}], False)


def test_ask_persists_two_messages_citations_and_trace():
    store = _Store()
    result = RagConversationService(store, _AnswerService()).ask(
        user_id=7, query="  什么是一次函数？  ", session_id=None,
        filters=RetrievalFilters(grade_band="初中"))

    assert store.created == [(7, "student", "什么是一次函数？")]
    assert [message[1] for message in store.messages] == ["user", "assistant"]
    assert store.messages[1][3]["citations"][0]["source_name"] == "教材"
    assert store.traces[0]["candidates"] == [{"source_name": "教材", "score": 0.91}]
    assert result["session_id"] == 19


def test_ask_rejects_session_not_owned_by_student():
    with pytest.raises(ValueError, match="无权访问"):
        RagConversationService(_Store(), _AnswerService()).ask(
            user_id=7, query="测试", session_id=20, filters=RetrievalFilters())


def test_ask_rejects_empty_or_oversized_query():
    service = RagConversationService(_Store(), _AnswerService())
    with pytest.raises(ValueError, match="不能为空"):
        service.ask(user_id=7, query="  ", session_id=None, filters=RetrievalFilters())
    with pytest.raises(ValueError, match="2000"):
        service.ask(user_id=7, query="a" * 2001, session_id=None, filters=RetrievalFilters())


def test_profile_action_returns_only_server_computed_learning_summary():
    store = _Store()
    result = RagConversationService(store, _AnswerService()).ask(
        user_id=7, query="我的薄弱点", session_id=None, filters=RetrievalFilters(),
        learning_action="study_priority", learner_context={"weak_points": [{
            "knowledge_point": "数学::一次函数", "mastery_score": 0.42, "attempt_count": 3
        }]})
    assert "一次函数" in result["answer"]
    assert result["citations"][0]["source_name"] == "你的学习记录（系统统计）"


def test_answer_stream_chunks_preserve_text_and_limit_chunk_size():
    answer = "先看定义。\n再代入计算：|-3|=3，答案是 B。"
    chunks = list(_answer_stream_chunks(answer, target_size=6))
    assert "".join(chunks) == answer
    assert chunks and all(0 < len(chunk) <= 6 for chunk in chunks)
