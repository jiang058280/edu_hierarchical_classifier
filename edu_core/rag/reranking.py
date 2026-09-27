"""Cohere-style /rerank 协议适配；非 OpenAI 标准接口，不自动启用。"""

from __future__ import annotations

import math
from typing import Protocol

import httpx

from edu_core.config.settings import Settings
from edu_core.rag.providers import ProviderConfigurationError, ProviderResponseError


class RerankerProvider(Protocol):
    def score(self, query: str, documents: list[str]) -> list[float]:
        """返回与输入顺序一致的全部相关性分数。"""


def validate_scores(scores: list[float], count: int) -> list[float]:
    if not isinstance(scores, list) or len(scores) != count:
        raise ProviderResponseError("Reranker 分数数量不匹配")
    if any(type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1
           for score in scores):
        raise ProviderResponseError("Reranker 分数必须为 0 到 1 的有限数值")
    return [float(score) for score in scores]


class HTTPRerankerProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

    def score(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        settings = self.settings
        if settings.rag_reranker_provider != "cohere_compatible":
            raise ProviderConfigurationError("Reranker 仅支持 cohere_compatible 协议")
        if not settings.rag_reranker_base_url or not settings.rag_reranker_model:
            raise ProviderConfigurationError("Reranker 端点或模型尚未配置")
        headers = ({"Authorization": f"Bearer {settings.rag_reranker_api_key}"}
                   if settings.rag_reranker_api_key else {})
        try:
            response = httpx.post(
                settings.rag_reranker_base_url.rstrip("/") + "/rerank", headers=headers,
                json={"model": settings.rag_reranker_model, "query": query,
                      "documents": documents, "top_n": len(documents)},
                timeout=settings.rag_request_timeout_seconds, follow_redirects=False,
            )
        except httpx.RequestError:
            raise ProviderResponseError("Reranker 请求失败或超时") from None
        if not 200 <= response.status_code < 300:
            # 不回显 URL、响应正文或供应商消息，避免泄露密钥和资料。
            raise ProviderResponseError(f"Reranker 服务返回 HTTP {response.status_code}")
        try:
            results = response.json()["results"]
            if not isinstance(results, list) or len(results) != len(documents):
                raise ValueError
            indexed = {}
            for item in results:
                index = item["index"]
                if type(index) is not int or not 0 <= index < len(documents) or index in indexed:
                    raise ValueError
                indexed[index] = item["relevance_score"]
            return validate_scores([indexed[i] for i in range(len(documents))], len(documents))
        except (ValueError, KeyError, TypeError):
            raise ProviderResponseError("Reranker 服务返回格式无效") from None
