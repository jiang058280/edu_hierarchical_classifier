import asyncio
import json

import httpx
import pytest

from edu_core.config.settings import Settings
from edu_core.rag.streaming import stream_chat
from edu_core.rag.providers import ProviderResponseError
from edu_core.rag.conversations import RagConversationService
from edu_core.rag.generation import RagAnswer
from edu_core.rag.retrieval import RetrievalFilters
from tests.test_rag_conversations import _Store
from tests.test_rag_generation import _service, _candidate


def frame(content="", finish=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": content},
                                                "finish_reason": finish}]}) + "\n\n"


def install_response(monkeypatch, text, status=200):
    client_class = httpx.AsyncClient

    def handle(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(status, text=text)

    monkeypatch.setattr("edu_core.rag.streaming.httpx.AsyncClient",
                        lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))


def config():
    return Settings(_env_file=None, rag_llm_base_url="https://test.example/v1",
                    rag_llm_model="test", rag_llm_api_key="test-key")


def test_native_provider_yields_delta_and_requires_completion(monkeypatch):
    install_response(monkeypatch, frame("第一段") + frame("第二段") + frame(finish="stop") + "data: [DONE]\n\n")

    async def run():
        return [part async for part in stream_chat(config(), [])]
    assert asyncio.run(run()) == ["第一段", "第二段"]


@pytest.mark.parametrize("text", [frame("半截"), frame("半截", "length") + "data: [DONE]\n\n",
    "data: not-json\n\n", "data: [DONE]\n\n", frame("半截") + "data: [DONE]\n\n",
    frame(finish="stop") + "data: [DONE]\n\n"])
def test_incomplete_or_malformed_provider_fails(monkeypatch, text):
    install_response(monkeypatch, text)

    async def run():
        return [part async for part in stream_chat(config(), [])]
    with pytest.raises(ProviderResponseError):
        asyncio.run(run())


class AnswerService:
    def __init__(self, fail=False):
        self.fail, self.closed = fail, False

    async def stream_answer(self, *args, **kwargs):
        try:
            yield "token", "第一段"
            if self.fail:
                raise ProviderResponseError("上游中断")
            yield "token", "第二段"
            yield "answer", RagAnswer("第一段第二段", [{"source_name": "教材", "score": .9, "kb_version": "v1"}], False)
        finally:
            self.closed = True


def test_conversation_does_not_persist_partial_tokens():
    store, answer = _Store(), AnswerService()

    async def run():
        stream = RagConversationService(store, answer).ask_stream(
            user_id=7, query="问题", session_id=None, filters=RetrievalFilters())
        assert (await anext(stream))[0] == "session"
        assert (await anext(stream))[0] == "token"
        assert [item[1] for item in store.messages] == ["user"]
        rest = [item async for item in stream]
        assert [item[0] for item in rest] == ["token", "citations", "done"]
        assert rest[-1][1]["message_id"] is not None
    asyncio.run(run())
    assert store.messages[-1][2] == "第一段第二段"
    assert store.traces[-1]["failure_stage"] is None and answer.closed


@pytest.mark.parametrize("cancel", [True, False])
def test_stream_cancellation_or_error_closes_upstream_without_success(cancel):
    store, answer = _Store(), AnswerService(fail=not cancel)

    async def run():
        stream = RagConversationService(store, answer).ask_stream(
            user_id=7, query="问题", session_id=None, filters=RetrievalFilters())
        await anext(stream)
        await anext(stream)
        if cancel:
            await stream.aclose()
        else:
            with pytest.raises(ProviderResponseError):
                await anext(stream)
    asyncio.run(run())
    assert answer.closed
    assert [item[1] for item in store.messages] == ["user"]
    assert store.traces[-1]["failure_stage"] == "stream_incomplete"


def test_stream_rejects_other_users_session():
    store = _Store()

    async def run():
        return [event async for event in RagConversationService(store, AnswerService()).ask_stream(
            user_id=7, query="问题", session_id=20, filters=RetrievalFilters())]
    with pytest.raises(ValueError, match="无权访问"):
        asyncio.run(run())
    assert not store.messages


def test_no_evidence_stream_refuses_without_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("不应调用模型")
    monkeypatch.setattr("edu_core.rag.streaming.httpx.AsyncClient", forbidden)

    async def run():
        return [event async for event in _service([_candidate(.1)]).stream_answer(
            "问题", role="student", filters=RetrievalFilters())]
    result = asyncio.run(run())
    assert result[-1][0] == "answer" and result[-1][1].refused
    assert not result[-1][1].citations


def test_http_failure_never_echoes_provider_body(monkeypatch):
    install_response(monkeypatch, "private secret", status=503)

    async def run():
        return [part async for part in stream_chat(config(), [])]
    with pytest.raises(ProviderResponseError) as error:
        asyncio.run(run())
    assert "503" in str(error.value) and "secret" not in str(error.value)


def test_reasoning_fields_are_not_sent_as_answer(monkeypatch):
    reasoning = 'data: {"choices":[{"index":0,"delta":{"reasoning_content":"internal"},"finish_reason":null}]}\n\n'
    install_response(monkeypatch, reasoning + frame("回答", "stop") + "data: [DONE]\n\n")

    async def run():
        return [part async for part in stream_chat(config(), [])]
    assert asyncio.run(run()) == ["回答"]
