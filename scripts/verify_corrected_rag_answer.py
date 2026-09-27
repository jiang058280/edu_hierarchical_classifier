"""Read-only, real-provider smoke test for a staged test KB. Never activates it.

An explicit in-process scope selects the staged KB, with real MySQL publication
and role filtering. Local question lookup is excluded to exercise document RAG.
This is not a student HTTP end-to-end test or a formal accuracy benchmark.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.config.settings import get_settings
from edu_core.rag.generation import RagAnswerService
from edu_core.rag.providers import OpenAICompatibleChatProvider
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters
from edu_core.storage.stores import StoreBundle


class ScopedReadStore:
    def __init__(self, store, version):
        self.store, self.version = store, version

    def get_active_version(self):
        return self.version

    def get_retrieval_chunks(self, *args, **kwargs):
        if kwargs['kb_version_id'] != self.version['id']:
            raise ValueError('测试范围发生变化')
        return self.store.get_retrieval_chunks(*args, **kwargs)

    def list_local_question_knowledge(self, **kwargs):
        return []  # Explicitly target document RAG, not a fabricated local miss.


class CountingChat:
    def __init__(self, settings):
        self.provider, self.calls = OpenAICompatibleChatProvider(settings), 0

    def complete(self, messages):
        self.calls += 1
        return self.provider.complete(messages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--grounding-check', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error('需要 --execute，将调用真实 Embedding 和回答模型')
    if args.report.exists():
        parser.error('报告已存在，请指定新路径')
    store, settings = StoreBundle().rag, get_settings()
    if args.grounding_check:
        settings = settings.model_copy(update={'rag_grounding_check_enabled': True})
    document = store.get_owned_document(24, 33)
    version = store.get_owned_version(3, 33)
    if not document or document['status'] != 'PUBLISHED' or document['kb_version_id'] != 3 or not version:
        raise RuntimeError('校对版或所属测试版本状态不符')
    active_before = store.get_active_version()
    chat = CountingChat(settings)
    service = RagAnswerService(RagRetrievalService(ScopedReadStore(store, version), settings), settings, chat)
    report = {'scope': 'in_process_staged_version_3_document_rag_only',
              'not_student_http_e2e': True, 'not_formal_accuracy_evaluation': True,
              'evidence_threshold': settings.rag_min_evidence_score,
              'grounding_check_enabled': settings.rag_grounding_check_enabled, 'cases': []}
    cases = [
        ('calculation', '根据一次函数教学资料，已知 y = 2x + 1，x = 2 时如何计算？', '初中', '初二', False),
        ('monotonicity', '一次函数 y = -2x + 4 随 x 的增大怎样变化？', '初中', '初二', False),
        ('wrong_scope', '已知 y = 2x + 1，x = 2 时如何计算？', '高中', '高三', True),
    ]
    try:
        for name, query, band, grade, expect_refused in cases:
            trace, before, start = {}, chat.calls, time.perf_counter()
            answer = service.answer(query, role='teacher',
                filters=RetrievalFilters(subject='数学', grade_band=band, grade=grade), evaluation_trace=trace)
            ids = sorted({c['metadata']['document_id'] for c in trace.get('candidates', [])})
            citations_ok = all(c['source_name'] == document['source_name'] and c['kb_version'] == version['version']
                               for c in answer.citations)
            checks = trace.get('grounding_checks', [])
            generation_ok = (chat.calls - before in (2, 4) and bool(checks) and checks[-1]['supported']) if (
                settings.rag_grounding_check_enabled) else chat.calls == before + 1
            passed = (answer.refused and not answer.citations and chat.calls == before) if expect_refused else (
                not answer.refused and bool(answer.citations) and citations_ok and ids == [24] and generation_ok)
            report['cases'].append({'case': name, 'query': query, 'answer': asdict(answer),
                'document_ids': ids, 'trace': trace, 'chat_calls': chat.calls - before,
                'elapsed_seconds': round(time.perf_counter() - start, 3), 'structural_checks_passed': passed})
        report['structural_checks_passed'] = all(c['structural_checks_passed'] for c in report['cases'])
    finally:
        active_after = store.get_active_version()
        report['active_version_unchanged'] = (active_before or {}).get('id') == (active_after or {}).get('id')
        report['active_version_id'] = (active_after or {}).get('id')
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'structural_checks_passed': report['structural_checks_passed'],
                      'active_version_unchanged': report['active_version_unchanged'], 'chat_calls': chat.calls}))
    if not report['structural_checks_passed'] or not report['active_version_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
