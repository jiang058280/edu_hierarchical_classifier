import httpx
import pytest

from edu_core.config.settings import Settings
from edu_core.rag.providers import ProviderConfigurationError, ProviderResponseError
from edu_core.rag.reranking import HTTPRerankerProvider, validate_scores
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from tests.test_rag_retrieval import FakeEmbedding, FakeRetriever, FakeStore
from tests.test_rag_generation import _candidate, _service


def settings(**kwargs):
    return Settings(_env_file=None, rag_enabled=True, rag_reranker_provider="cohere_compatible",
                    rag_reranker_base_url="https://rerank.example/v2", rag_reranker_api_key="test-secret",
                    rag_reranker_model="test-model", **kwargs)


def test_reranker_preserves_input_mapping_and_request_contract(monkeypatch):
    def post(url, **kwargs):
        assert url == "https://rerank.example/v2/rerank"
        assert kwargs["json"] == {"model": "test-model", "query": "函数", "documents": ["a", "b"], "top_n": 2}
        assert kwargs["follow_redirects"] is False
        return httpx.Response(200, json={"results": [{"index": 1, "relevance_score": .9},
                                                    {"index": 0, "relevance_score": .2}]})
    monkeypatch.setattr("edu_core.rag.reranking.httpx.post", post)
    assert HTTPRerankerProvider(settings()).score("函数", ["a", "b"]) == [.2, .9]


@pytest.mark.parametrize("results", [[], [{"index": 0, "relevance_score": .9}] * 2,
    [{"index": -1, "relevance_score": .9}, {"index": 0, "relevance_score": .2}],
    [{"index": True, "relevance_score": .9}, {"index": 0, "relevance_score": .2}],
    [{"index": 0, "relevance_score": "0.9"}, {"index": 1, "relevance_score": .2}],
    [{"index": 0, "relevance_score": 2}, {"index": 1, "relevance_score": .2}],
])
def test_bad_indices_and_scores_fail_closed(monkeypatch, results):
    monkeypatch.setattr("edu_core.rag.reranking.httpx.post",
                        lambda *_a, **_k: httpx.Response(200, json={"results": results}))
    with pytest.raises(ProviderResponseError):
        HTTPRerankerProvider(settings()).score("x", ["a", "b"])


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_errors_do_not_echo_secrets(monkeypatch, status):
    monkeypatch.setattr("edu_core.rag.reranking.httpx.post",
                        lambda *_a, **_k: httpx.Response(status, text="test-secret private document"))
    with pytest.raises(ProviderResponseError) as exc:
        HTTPRerankerProvider(settings()).score("x", ["a"])
    assert str(status) in str(exc.value) and "secret" not in str(exc.value)


def test_timeout_is_controlled(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("test-secret")
    monkeypatch.setattr("edu_core.rag.reranking.httpx.post", fail)
    with pytest.raises(ProviderResponseError, match="超时"):
        HTTPRerankerProvider(settings()).score("x", ["a"])


def test_model_is_not_called_without_explicit_threshold():
    service = RagRetrievalService(FakeStore(), settings(), FakeEmbedding(), FakeRetriever())
    with pytest.raises(ProviderConfigurationError, match="独立"):
        service.debug("x", role="teacher", filters=RetrievalFilters())


def test_reranking_order_and_post_model_permission_check():
    class Store(FakeStore):
        calls = 0

        def get_retrieval_chunks(self, ids, **kwargs):
            self.calls += 1
            row = super().get_retrieval_chunks(ids, **kwargs)[0]
            return [row, row | {"id": 9, "content": "另一条资料"}] if self.calls == 1 else [row]

    class Reranker:
        def score(self, query, documents):
            assert documents == ["一次函数 y=kx+b 的图像是直线。", "另一条资料"]
            return [.7, .95]

    service = RagRetrievalService(Store(), settings(rag_reranker_min_score=.8),
                                  FakeEmbedding(), FakeRetriever(), Reranker())
    result = service.debug("函数", role="teacher", filters=RetrievalFilters())
    assert [item["chunk_id"] for item in result["candidates"]] == [8]
    assert result["candidates"][0]["score"] == .7
    assert result["candidates"][0]["score_kind"] == "reranker"
    assert result["candidates"][0]["vector_score"] == 0.1


def test_reranker_threshold_is_not_vector_threshold():
    service = _service([])
    candidate = _candidate(.7) | {"score_kind": "reranker"}
    assert service._meets_evidence_threshold(candidate) is False  # 尚未配置
    service.settings.rag_reranker_min_score = .8
    assert service._meets_evidence_threshold(candidate) is False
    assert service._meets_evidence_threshold(candidate | {"score": .81}) is True
    assert service._meets_evidence_threshold(candidate | {"score_kind": "rrf"}) is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, -.1])
def test_nonfinite_and_invalid_scores_are_rejected(value):
    with pytest.raises(ProviderResponseError):
        validate_scores([value], 1)


def test_non_json_response_is_controlled(monkeypatch):
    monkeypatch.setattr("edu_core.rag.reranking.httpx.post",
                        lambda *_a, **_k: httpx.Response(200, text="not json"))
    with pytest.raises(ProviderResponseError, match="格式"):
        HTTPRerankerProvider(settings()).score("x", ["a"])


def test_reranker_changes_rank_before_top_k():
    class Store(FakeStore):
        def get_retrieval_chunks(self, ids, **kwargs):
            row = super().get_retrieval_chunks(ids, **kwargs)[0]
            return [row, row | {"id": 9, "content": "关键词候选"}]

    class Reranker:
        def score(self, query, documents):
            return [.1, .9]

    service = RagRetrievalService(Store(), settings(rag_reranker_min_score=.8, rag_rerank_top_k=1),
                                  FakeEmbedding(), FakeRetriever(), Reranker())
    rows = service.debug("函数", role="teacher", filters=RetrievalFilters())["candidates"]
    assert [row["chunk_id"] for row in rows] == [9]
    assert rows[0]["score"] == .9 and rows[0]["vector_score"] == .01
