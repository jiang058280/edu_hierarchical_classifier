"""R1.2 Provider 配置与 OpenAI-compatible 协议测试。"""

import pytest
import httpx

from edu_core.config.settings import Settings
from edu_core.rag.providers import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleEmbeddingProvider,
    ProviderConfigurationError,
    ProviderResponseError,
)


def test_embedding_requires_explicit_provider_configuration():
    with pytest.raises(ProviderConfigurationError, match="Embedding"):
        OpenAICompatibleEmbeddingProvider(Settings(_env_file=None)).embed(["一次函数"])


def test_chat_requires_explicit_provider_configuration():
    with pytest.raises(ProviderConfigurationError, match="LLM"):
        OpenAICompatibleChatProvider(Settings(_env_file=None)).complete([
            {"role": "user", "content": "解释一次函数"},
        ])


def test_empty_embedding_does_not_make_network_request():
    assert OpenAICompatibleEmbeddingProvider(Settings(_env_file=None)).embed([]) == []


def test_embedding_batches_requests_and_supplies_configured_dimension(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        return httpx.Response(200, json={"data": [{"embedding": [float(index)]} for index in range(len(json["input"]))]})

    monkeypatch.setattr("edu_core.rag.providers.httpx.post", fake_post)
    settings = Settings(_env_file=None, rag_embedding_base_url="https://embedding.example/v1",
                        rag_embedding_api_key="test-key", rag_embedding_model="text-embedding-v4",
                        rag_embedding_dimension=1024, rag_embedding_batch_size=2)
    assert OpenAICompatibleEmbeddingProvider(settings).embed(["a", "b", "c", "d", "e"]) == [
        [0.0], [1.0], [0.0], [1.0], [0.0],
    ]
    assert [call["input"] for call in calls] == [["a", "b"], ["c", "d"], ["e"]]
    assert all(call["dimensions"] == 1024 for call in calls)


def test_embedding_error_includes_safe_provider_code_and_message(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return httpx.Response(400, json={"code": "InvalidParameter", "message": "input count exceeds limit"})

    monkeypatch.setattr("edu_core.rag.providers.httpx.post", fake_post)
    settings = Settings(_env_file=None, rag_embedding_base_url="https://embedding.example/v1",
                        rag_embedding_api_key="test-key", rag_embedding_model="text-embedding-v4")
    with pytest.raises(ProviderResponseError, match="InvalidParameter: input count exceeds limit"):
        OpenAICompatibleEmbeddingProvider(settings).embed(["a"])
