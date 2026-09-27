"""OpenAI-compatible Provider 适配层。

业务服务只依赖协议；端点、模型与密钥由 Settings 注入，严禁写入日志或数据库。
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from edu_core.config.settings import Settings


class ProviderConfigurationError(RuntimeError):
    """Provider 未启用或缺少必要配置。"""


class ProviderResponseError(RuntimeError):
    """Provider 响应不符合契约。"""


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回向量；失败时抛受控异常。"""


class ChatProvider(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """返回非流式文本；R2 再封装流式协议。"""


class OpenAICompatibleEmbeddingProvider:
    """`POST /embeddings` 的最小兼容封装。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._require_config(self.settings.rag_embedding_base_url,
                             self.settings.rag_embedding_api_key,
                             self.settings.rag_embedding_model, "Embedding")
        batch_size = max(1, int(self.settings.rag_embedding_batch_size))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(self._embed_batch(texts[start:start + batch_size]))
        if len(vectors) != len(texts) or any(not isinstance(v, list) for v in vectors):
            raise ProviderResponseError("Embedding 数量或向量格式不匹配")
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            self.settings.rag_embedding_base_url.rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {self.settings.rag_embedding_api_key}"},
            json={
                "model": self.settings.rag_embedding_model,
                "input": texts,
                "dimensions": int(self.settings.rag_embedding_dimension),
            },
            timeout=self.settings.rag_request_timeout_seconds,
        )
        if response.status_code >= 400:
            raise ProviderResponseError(self._response_error("Embedding", response))
        try:
            data = response.json().get("data", [])
            return [item["embedding"] for item in data]
        except (AttributeError, KeyError, TypeError) as exc:
            raise ProviderResponseError("Embedding 服务返回格式无效") from exc

    @staticmethod
    def _response_error(label: str, response: httpx.Response) -> str:
        """仅保留可行动的服务端错误字段，避免将密钥或完整响应回显到页面。"""
        detail = ""
        try:
            payload: Any = response.json()
            if isinstance(payload, dict):
                code = str(payload.get("code", "")).strip()
                message = str(payload.get("message", "")).strip()
                fragments = [part for part in (code, message) if part]
                detail = ": ".join(fragments)
        except (ValueError, TypeError):
            pass
        if detail:
            detail = detail.replace("\r", " ").replace("\n", " ")[:240]
            return f"{label} 服务返回 HTTP {response.status_code}（{detail}）"
        return f"{label} 服务返回 HTTP {response.status_code}"

    @staticmethod
    def _require_config(base_url: str, api_key: str, model: str, label: str) -> None:
        if not base_url or not api_key or not model:
            raise ProviderConfigurationError(f"{label} Provider 尚未完成配置")


class OpenAICompatibleChatProvider:
    """`POST /chat/completions` 的最小兼容封装，供 R2 使用。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def complete(self, messages: list[dict[str, str]]) -> str:
        OpenAICompatibleEmbeddingProvider._require_config(
            self.settings.rag_llm_base_url, self.settings.rag_llm_api_key,
            self.settings.rag_llm_model, "LLM")
        response = httpx.post(
            self.settings.rag_llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.rag_llm_api_key}"},
            json={"model": self.settings.rag_llm_model, "messages": messages, "stream": False},
            timeout=self.settings.rag_request_timeout_seconds,
        )
        if response.status_code >= 400:
            raise ProviderResponseError(OpenAICompatibleEmbeddingProvider._response_error("LLM", response))
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as exc:
            raise ProviderResponseError("LLM 服务返回格式无效") from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderResponseError("LLM 服务返回空内容")
        return content
