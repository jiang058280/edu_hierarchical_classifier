import asyncio

import pytest

from edu_core.config.settings import Settings
from edu_core.rag.conversations import RagConversationService
from edu_core.rag.generation import RagAnswer, RagAnswerService
from edu_core.rag.memory import completed_questions, contextual_query
from edu_core.rag.retrieval import RetrievalFilters
from tests.test_rag_generation import FakeChat, FakeRetrieval, _candidate
from tests.test_rag_conversations import _Store


def history():
    return [{"role": "user", "content": "一次函数是什么"},
            {"role": "assistant", "content": "历史错误答案不能当证据", "refused": False}]


def test_only_completed_user_questions_are_used():
    assert completed_questions(history()) == ["一次函数是什么"]
    assert completed_questions(history() + [{"role": "user", "content": "未完成"}]) == []
    rows = history()
    rows[-1]["refused"] = True
    assert completed_questions(rows) == []
    assert completed_questions([history()[0], history()[0], history()[1]]) == []
    assert len(completed_questions(history() * 10)) == 3


@pytest.mark.parametrize("query", ["那斜率呢？", "继续讲解", "这道题怎么解", "上一步为什么这样算"])
def test_explicit_followup_gets_topic(query):
    result = contextual_query(query, ["一次函数是什么"])
    assert "一次函数" in result and result.endswith(query)


@pytest.mark.parametrize("query", ["什么是光合作用", "换个话题，那化学呢？", "它" * 2000])
def test_independent_or_oversized_query_is_not_expanded(query):
    assert contextual_query(query, ["一次函数是什么"]) == query


def test_followup_retrieves_fresh_evidence_and_never_local_shortcuts():
    class Retrieval(FakeRetrieval):
        def debug(self, query, **kwargs):
            assert "一次函数" in query and "那斜率呢" in query
            assert kwargs["filters"].grade == "初三"
            return super().debug(query, **kwargs)
        def local_question_candidates(self, *args, **kwargs):
            pytest.fail("追问不能直接复用题库标准答案")
    chat, trace = FakeChat(), {}
    service = RagAnswerService(Retrieval([_candidate(.1)]),
                              Settings(_env_file=None, rag_conversation_memory_enabled=True), chat)
    result = service.answer("那斜率呢？", role="student", filters=RetrievalFilters(grade="初三"),
                            conversation_questions=["一次函数是什么"], evaluation_trace=trace)
    assert result.refused and chat.messages is None
    assert trace["contextualized"] is True


class HistoryStore(_Store):
    def recent_student_messages(self, session_id, user_id):
        assert (session_id, user_id) == (19, 7)
        assert self.messages == []  # 当前问题还没落库，不能重复进入历史。
        return history()


class Answer:
    settings = Settings(_env_file=None, rag_conversation_memory_enabled=True)
    def answer(self, query, **kwargs):
        assert kwargs["conversation_questions"] == ["一次函数是什么"]
        return RagAnswer("新的回答", [], True)
    async def stream_answer(self, query, **kwargs):
        yield "answer", self.answer(query, **kwargs)


@pytest.mark.parametrize("streaming", [False, True])
def test_both_conversation_paths_read_owned_history_before_current_message(streaming):
    service = RagConversationService(HistoryStore(), Answer())
    kwargs = dict(user_id=7, query="那斜率呢？", session_id=19, filters=RetrievalFilters())
    if streaming:
        async def run():
            return [event async for event in service.ask_stream(**kwargs)]
        asyncio.run(run())
    else:
        service.ask(**kwargs)


@pytest.mark.parametrize("streaming", [False, True])
def test_wrong_owner_rejected_before_reading_history(streaming):
    service = RagConversationService(HistoryStore(), Answer())
    kwargs = dict(user_id=8, query="那斜率呢？", session_id=19, filters=RetrievalFilters())
    with pytest.raises(ValueError, match="无权访问"):
        if streaming:
            async def run():
                return [event async for event in service.ask_stream(**kwargs)]
            asyncio.run(run())
        else:
            service.ask(**kwargs)


def test_disabled_memory_preserves_original_query():
    class Retrieval(FakeRetrieval):
        def debug(self, query, **kwargs):
            assert query == "那斜率呢？"
            return super().debug(query, **kwargs)
    service = RagAnswerService(Retrieval([]), Settings(_env_file=None), FakeChat())
    service.answer("那斜率呢？", role="student", filters=RetrievalFilters(),
                   conversation_questions=["一次函数是什么"])


def test_prompt_has_topic_but_not_old_assistant_answer():
    class Retrieval(FakeRetrieval):
        def debug(self, query, **kwargs):
            return {"normalized_query": query, "candidates": self.candidates}
    chat = FakeChat()
    service = RagAnswerService(Retrieval([_candidate()]),
                              Settings(_env_file=None, rag_conversation_memory_enabled=True), chat)
    result = service.answer("那斜率呢？", role="student", filters=RetrievalFilters(),
                            conversation_questions=completed_questions(history()))
    assert not result.refused
    assert "一次函数是什么" in chat.messages[1]["content"]
    assert "那斜率呢" in chat.messages[1]["content"]
    assert "历史错误答案" not in str(chat.messages)
    assert result.citations[0]["kb_version"] == "kb-v1"
