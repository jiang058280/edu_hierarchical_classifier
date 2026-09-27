"""异步聊天 SSE 读取：必须收到 stop 和 DONE 才视为完整生成。"""

import json

import httpx

from edu_core.config.settings import Settings
from edu_core.rag.providers import ProviderConfigurationError, ProviderResponseError


async def stream_chat(settings: Settings, messages: list[dict]):
    if not settings.rag_llm_base_url or not settings.rag_llm_model or not settings.rag_llm_api_key:
        raise ProviderConfigurationError("LLM 流式 Provider 尚未完成配置")
    stopped, size = False, 0
    try:
        async with httpx.AsyncClient(timeout=settings.rag_request_timeout_seconds) as client:
            async with client.stream(
                "POST", settings.rag_llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.rag_llm_api_key}"},
                json={"model": settings.rag_llm_model, "messages": messages, "stream": True},
            ) as response:
                if response.status_code != 200:
                    raise ProviderResponseError(f"LLM 流式服务返回 HTTP {response.status_code}")
                data_lines = []
                async for line in response.aiter_lines():
                    if len(line) > 200000:
                        raise ProviderResponseError("LLM 流式事件过大")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        if sum(map(len, data_lines)) > 200000:
                            raise ProviderResponseError("LLM 流式事件过大")
                    elif line == "" and data_lines:
                        data, data_lines = "\n".join(data_lines), []
                        if data == "[DONE]":
                            if not stopped or not size:
                                raise ProviderResponseError("LLM 流式回答未完整结束")
                            return
                        try:
                            payload = json.loads(data)
                            if not isinstance(payload, dict) or "error" in payload:
                                raise ValueError
                            choices = payload.get("choices")
                            if choices == []:  # 可选 usage 帧
                                continue
                            choice = choices[0]
                            if len(choices) != 1 or choice.get("index", 0) != 0:
                                raise ValueError
                            delta = choice["delta"]
                            content = delta.get("content")
                            content = "" if content is None else content
                            if not isinstance(content, str) or delta.get("tool_calls") or delta.get("refusal"):
                                raise ValueError
                            finish = choice.get("finish_reason")
                            if finish not in (None, "stop") or stopped and content:
                                raise ValueError
                        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                            raise ProviderResponseError("LLM 流式事件无效或生成被截断") from None
                        if content:
                            size += len(content)
                            if size > 100000:
                                raise ProviderResponseError("LLM 流式回答超过长度限制")
                            yield content
                        stopped = stopped or finish == "stop"
                raise ProviderResponseError("LLM 流式连接提前结束")
    except httpx.RequestError:
        raise ProviderResponseError("LLM 流式请求失败或超时") from None
