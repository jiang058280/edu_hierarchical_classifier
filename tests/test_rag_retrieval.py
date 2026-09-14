"""R2.1 检索标准化、版本窗口与 MySQL 二次授权过滤测试。"""

from __future__ import annotations

import pytest

from edu_core.config.settings import Settings
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from edu_core.rag.retrieval.service import normalize_query, query_variants, route_intent


class FakeStore:
    def __init__(self, active=True):
        self.active, self.received = active, None

    def get_active_version(self):
        return {"id": 3, "version": "kb-demo"} if self.active else None

    def get_retrieval_chunks(self, ids, **kwargs):
        self.received = (ids, kwargs)
        return [{"id": 8, "document_id": 2, "parent_chunk_id": 7, "subject": "数学", "grade_band": "初中",
                 "grade": "初三", "knowledge_node_id": 9, "chapter": "函数", "source_name": "函数讲义.md",
                 "page_number": 2, "kb_version": "kb-demo", "status": "PUBLISHED", "allowed_roles": ["teacher"],
                 "content_hash": "a" * 64, "content": "一次函数 y=kx+b 的图像是直线。"}]

    def list_local_question_knowledge(self, **_kwargs):
        return []


class FakeEmbedding:
    def embed(self, texts):
        return [[float(index), 0.0] for index, _ in enumerate(texts, start=1)]


class FakeRetriever:
    def search(self, vector, *, limit):
        return [{"id": 8, "score": vector[0] / 10}, {"id": 9, "score": 0.01}]


def _service(active=True):
    store = FakeStore(active)
    settings = Settings(_env_file=None, rag_enabled=True, rag_top_k=4, rag_rerank_top_k=2)
    return RagRetrievalService(store, settings, FakeEmbedding(), FakeRetriever()), store


def test_normalization_variants_and_intent_router():
    assert normalize_query("  一次函数\n 是什么？ ") == "一次函数 是什么？"
    assert query_variants("请解释一次函数") == ["请解释一次函数", "一次函数"]
    assert route_intent("一次函数的定义") == "faq_preferred"
    assert route_intent("请讲解一次函数图像") == "document_question"


def test_debug_uses_active_version_and_passes_all_hard_filters():
    service, store = _service()
    result = service.debug("请解释一次函数", role="teacher", filters=RetrievalFilters(
        subject="数学", grade_band="初中", grade="初三", knowledge_node_id=9))
    assert result["active_version"] == "kb-demo"
    assert result["candidates"][0]["metadata"]["source_name"] == "函数讲义.md"
    ids, filters = store.received
    assert ids == [8, 9] and filters["kb_version_id"] == 3 and filters["role"] == "teacher"
    assert filters["subject"] == "数学" and filters["knowledge_node_id"] == 9


def test_debug_refuses_when_rag_disabled_or_no_active_version():
    service, _ = _service(active=False)
    assert service.debug("函数", role="teacher", filters=RetrievalFilters())["reason"] == "当前没有已激活知识库版本"
    disabled = RagRetrievalService(FakeStore(), Settings(_env_file=None, rag_enabled=False), FakeEmbedding(), FakeRetriever())
    with pytest.raises(ValueError, match="未启用"):
        disabled.debug("函数", role="teacher", filters=RetrievalFilters())


def test_local_question_candidates_are_deterministic_and_do_not_use_embedding():
    service, store = _service()
    store.list_local_question_knowledge = lambda **_kwargs: [{
        "question_id": 3, "question_text": "已知函数 f(x)=x+1，求 f(2) 的值。", "answer": "3",
        "analysis": "代入计算", "knowledge_point": "数学::一次函数", "question_type": "解答题",
    }]
    hits = service.local_question_candidates("已知函数 f(x)=x+1，求 f(2) 的值。", filters=RetrievalFilters())
    assert hits[0]["question_id"] == 3 and hits[0]["score"] == 1.0
