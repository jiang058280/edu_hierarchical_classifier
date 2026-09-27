import math

import pytest

from edu_core.config.settings import Settings
from edu_core.rag.retrieval.hybrid import bm25, reciprocal_rank_fusion, tokenize
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from tests.test_rag_retrieval import FakeEmbedding, FakeStore


def test_bm25_uses_frequency_length_and_stable_ties():
    hits = bm25("function", [{"id": 2, "content": "function"},
                             {"id": 1, "content": "function"},
                             {"id": 3, "content": "other words only"}], limit=3)
    assert [hit["id"] for hit in hits] == [1, 2]
    assert hits[0]["score"] == pytest.approx(math.log1p(1.5 / 2.5) * 2.2 / (1 + 1.2 * (0.25 + 0.75 / (5 / 3))))
    assert bm25("", [{"id": 1, "content": ""}], limit=1) == []
    assert "一次" in tokenize("一次函数，ＡＢＣ") and "abc" in tokenize("ＡＢＣ")


def test_rrf_deduplicates_each_route_and_rewards_shared_hits():
    scores = reciprocal_rank_fusion([[1, 1, 2], [2, 3]], k=60)
    assert scores[1] == 1 / 61
    assert scores[2] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[2] > scores[1] > scores[3]


@pytest.mark.parametrize("limit,k1,b", [(0, 1.2, 0.75), (1, 0, 0.75), (1, 1.2, 2)])
def test_bm25_rejects_invalid_parameters(limit, k1, b):
    with pytest.raises(ValueError):
        bm25("x", [], limit=limit, k1=k1, b=b)


def test_hybrid_filters_both_routes_and_keeps_evidence_score_separate():
    class Store(FakeStore):
        def __init__(self):
            super().__init__()
            self.calls = []

        def get_retrieval_chunks(self, ids, **kwargs):
            self.calls.append((ids, kwargs))
            row = super().get_retrieval_chunks(ids, **kwargs)[0]
            rows = [row, row | {"id": 10, "content": "稀有知识词定义"}]
            return rows if ids is None else [row for row in rows if row["id"] in ids]

    class Dense:
        def search(self, vector, *, limit):
            return [{"id": 8, "score": 0.8}, {"id": 999, "score": 0.99}]

    store = Store()
    service = RagRetrievalService(store, Settings(_env_file=None, rag_enabled=True,
                                  rag_hybrid_enabled=True), FakeEmbedding(), Dense())
    result = service.debug("稀有知识词定义", role="teacher", filters=RetrievalFilters(grade_band="初中"))
    rows = {row["chunk_id"]: row for row in result["candidates"]}
    assert set(rows) == {8, 10}  # 不可见的向量命中不得泄露
    assert rows[10]["score"] == 0.0 and rows[10]["bm25_score"] > 0
    assert rows[8]["score"] == 0.8 and rows[8]["rrf_score"] > 0
    assert result["retrieval_mode"] == "bm25_rrf"
    assert store.calls[0][0] is None
    assert all(kwargs["grade_band"] == "初中" and kwargs["role"] == "teacher"
               and kwargs["kb_version_id"] == 3 for _, kwargs in store.calls)
