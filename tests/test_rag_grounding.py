import asyncio
import json

import pytest

from edu_core.rag.grounding import review_answer
from edu_core.rag.providers import ProviderResponseError, OpenAICompatibleChatProvider
from edu_core.rag.retrieval import RetrievalFilters
from edu_core.config.settings import Settings
from tests.test_rag_generation import _service, _candidate


class SequenceChat:
    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def complete(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


OK = json.dumps({'supported': True, 'unsupported_claims': []})
BAD = json.dumps({'supported': False, 'unsupported_claims': ['资料没有该定义']})


def service(chat):
    result = _service([_candidate()], chat)
    result.settings = result.settings.model_copy(update={'rag_grounding_check_enabled': True})
    return result


def ask(chat, trace=None):
    return service(chat).answer('一次函数是什么', role='student', filters=RetrievalFilters(), evaluation_trace=trace)


def test_review_uses_exact_context_and_accepted_answer():
    chat = SequenceChat('有据回答[1]', OK)
    trace = {}
    result = ask(chat, trace)
    assert not result.refused and result.answer == '有据回答[1]'
    request = json.loads(chat.calls[1][1]['content'])
    assert request['evidence'].startswith('[1] 一次函数通常写成')
    assert trace['grounding_checks'][-1]['supported'] is True


def test_unsupported_answer_is_rewritten_then_reviewed_again():
    chat = SequenceChat('新定义', BAD, '修正回答[1]', OK)
    result = ask(chat)
    assert not result.refused and result.answer == '修正回答[1]' and len(chat.calls) == 4


def test_repeated_unsupported_answer_is_refused_without_citations():
    chat = SequenceChat('新定义', BAD, '仍然无据', BAD)
    result = ask(chat)
    assert result.refused and not result.citations and '仍然无据' not in result.answer
    assert len(chat.calls) == 4


@pytest.mark.parametrize('raw', ['not json', '[]', '{"supported":"true","unsupported_claims":[]}',
    '{"supported":true,"unsupported_claims":["错误"]}', '{"supported":false,"unsupported_claims":[]}',
    '{"supported":true,"unsupported_claims":[],"extra":1}',
    ProviderResponseError('timeout')])
def test_invalid_or_unavailable_review_fails_closed(raw):
    chat = SequenceChat('待核验回答', raw)
    assert ask(chat).refused and len(chat.calls) == 2


def test_unknown_citation_cannot_be_approved_by_model():
    chat = SequenceChat()
    verdict = review_answer(chat, query='问题', context='[1] 资料', answer='结论[99]', citation_numbers={1})
    assert not verdict.supported and verdict.reason == 'unknown_citation' and not chat.calls


def test_rewrite_failure_does_not_return_unchecked_answer():
    assert ask(SequenceChat('无据回答', BAD, ProviderResponseError('failed'))).refused


def test_stream_holds_draft_until_review_finishes(monkeypatch):
    async def stream(*args):
        yield '不应展示的草稿'
    monkeypatch.setattr('edu_core.rag.generation.stream_chat', stream)
    chat = SequenceChat(BAD, '核验后回答[1]', OK)

    async def run():
        return [event async for event in service(chat).stream_answer(
            '问题', role='student', filters=RetrievalFilters())]
    events = asyncio.run(run())
    assert events[0] == ('token', '核验后回答[1]')
    assert len(events) == 2 and events[1][1].answer == events[0][1]


def test_stream_failed_review_emits_only_refusal(monkeypatch):
    async def stream(*args):
        yield '不应展示的草稿'
    monkeypatch.setattr('edu_core.rag.generation.stream_chat', stream)

    async def run():
        return [event async for event in service(SequenceChat('invalid')).stream_answer(
            '问题', role='student', filters=RetrievalFilters())]
    events = asyncio.run(run())
    assert events[-1][1].refused and events[0][1] == events[-1][1].answer
    assert '不应展示' not in events[0][1]


def test_chat_http_error_remains_a_controlled_provider_error(monkeypatch):
    import httpx
    monkeypatch.setattr('edu_core.rag.providers.httpx.post', lambda *args, **kwargs: httpx.Response(503))
    settings = Settings(_env_file=None, rag_llm_base_url='https://test.example/v1',
                        rag_llm_api_key='test', rag_llm_model='test')
    with pytest.raises(ProviderResponseError, match='503'):
        OpenAICompatibleChatProvider(settings).complete([])


def test_conversation_records_grounding_refusal_separately():
    from edu_core.rag.conversations import RagConversationService
    from tests.test_rag_conversations import _Store
    store = _Store()
    result = RagConversationService(store, service(SequenceChat('草稿', 'invalid'))).ask(
        user_id=7, query='问题', session_id=None, filters=RetrievalFilters())
    assert result['refused'] and not result['citations']
    assert store.traces[-1]['failure_stage'] == 'grounding'
