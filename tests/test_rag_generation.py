"""R2.2 证据阈值、引用构造与拒答测试。"""

from __future__ import annotations

from edu_core.config.settings import Settings
from edu_core.rag.generation import RagAnswerService
from edu_core.rag.retrieval import RetrievalFilters


class FakeRetrieval:
    def __init__(self, candidates):
        self.candidates = candidates

    def debug(self, *_args, **_kwargs):
        return {"normalized_query": "一次函数是什么", "candidates": self.candidates, "reason": "没有证据"}

    def local_question_candidates(self, *_args, **_kwargs):
        return []


class FakeChat:
    def __init__(self, response="一次函数可以写成 y=kx+b。"):
        self.response, self.messages = response, None

    def complete(self, messages):
        self.messages = messages
        return self.response


def _candidate(score=0.9, source="函数讲义.md", chapter="第一章", page=2):
    return {"content": "一次函数通常写成 y=kx+b，其中 k 和 b 是常数。", "score": score,
            "metadata": {"source_name": source, "chapter": chapter, "page_number": page, "kb_version": "kb-v1"}}


def _service(candidates, chat=None):
    settings = Settings(_env_file=None, rag_min_evidence_score=0.55, rag_max_context_chars=100)
    return RagAnswerService(FakeRetrieval(candidates), settings, chat or FakeChat())


def test_answer_uses_evidence_and_server_generated_citations():
    chat = FakeChat()
    result = _service([_candidate(), _candidate(0.8)], chat).answer("一次函数是什么", role="student", filters=RetrievalFilters())
    assert result.refused is False and result.answer == "一次函数可以写成 y=kx+b。"
    assert result.citations == [{"number": 1, "source_name": "函数讲义.md", "chapter": "第一章",
                                 "page_number": 2, "kb_version": "kb-v1", "score": 0.9}]
    assert "【资料】" in chat.messages[1]["content"] and "[1]" in chat.messages[1]["content"]
    assert "不得引入资料未定义的新术语" in chat.messages[1]["content"]


def test_answer_refuses_without_high_enough_evidence_without_calling_llm():
    chat = FakeChat()
    result = _service([_candidate(0.2)], chat).answer("一次函数是什么", role="student", filters=RetrievalFilters())
    assert result.refused is True and not result.citations
    assert chat.messages is None


def test_duplicate_source_fragments_share_a_resolvable_citation_number():
    service = _service([])
    context, citations = service._context_and_citations([
        _candidate(), _candidate(), _candidate(source="另一份.md")])
    assert context.count("[1]") == 2
    assert "[2]" in context and "[3]" not in context
    assert [c["number"] for c in citations] == [1, 2]


def test_skipped_empty_evidence_does_not_create_number_gaps():
    blank = _candidate() | {"content": " "}
    context, citations = _service([])._context_and_citations([blank, _candidate()])
    assert context.startswith("[1]") and citations[0]["number"] == 1


def test_same_named_source_from_distinct_versions_has_distinct_citations():
    first, second = _candidate(), _candidate()
    second["metadata"]["kb_version"] = "kb-v2"
    context, citations = _service([])._context_and_citations([first, second])
    assert "[1]" in context and "[2]" in context and len(citations) == 2


def test_answer_refuses_empty_generation():
    result = _service([_candidate()], FakeChat(" ")).answer("一次函数是什么", role="student", filters=RetrievalFilters())
    assert result.refused is True and result.citations == []


def test_socratic_answer_receives_anonymous_learning_summary_only():
    chat = FakeChat()
    _service([_candidate()], chat).answer("怎么判断增减性", role="student", filters=RetrievalFilters(),
        socratic=True, learner_context={"weak_points": [{"knowledge_point": "数学::一次函数", "mastery_score": .4, "attempt_count": 2}]})
    prompt = chat.messages[1]["content"]
    assert "苏格拉底式辅导" in prompt and "一次函数" in prompt and "姓名" not in prompt


def test_exact_local_question_answer_does_not_call_chat_provider():
    class LocalRetrieval(FakeRetrieval):
        def local_question_candidates(self, *_args, **_kwargs):
            return [{"question_id": 9, "question_text": "已知函数 f(x)=x+1，求 f(2) 的值。",
                     "answer": "3", "analysis": "代入 x=2 得 f(2)=2+1=3。",
                     "knowledge_point": "数学::一次函数", "question_type": "解答题", "score": 1.0}]

    chat = FakeChat()
    settings = Settings(_env_file=None, rag_local_question_min_score=.75)
    result = RagAnswerService(LocalRetrieval([]), settings, chat).answer(
        "已知函数 f(x)=x+1，求 f(2) 的值。", role="student", filters=RetrievalFilters())
    assert result.refused is False and "参考答案：3" in result.answer
    assert result.citations[0]["source_name"] == "本地题库 · 第 9 题"
    assert chat.messages is None


def test_local_question_answer_excludes_unsubmitted_assignment_question():
    class LocalRetrieval(FakeRetrieval):
        def local_question_candidates(self, *_args, **_kwargs):
            return [{"question_id": 9, "question_text": "函数题", "answer": "3", "analysis": "代入计算",
                     "knowledge_point": "数学::一次函数", "question_type": "解答题", "score": 1.0}]

    service = RagAnswerService(LocalRetrieval([]), Settings(_env_file=None), FakeChat())
    assert service._local_question_answer("函数题", RetrievalFilters(), {9}) is None
