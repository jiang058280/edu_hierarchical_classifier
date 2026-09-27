from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from edu_core.api import teacher
from edu_core.config.settings import Settings
from edu_core.rag.ingestion import RagIngestionService
from tests.test_rag_ingestion import FakeEmbedding, FakeIndex
from tests.test_rag_store import rag_store  # noqa: F401


def test_correction_endpoint_preserves_source_reembeds_and_requires_review(rag_store, monkeypatch, tmp_path):  # noqa: F811
    version = rag_store.create_version('correction-test', created_by=104)
    original = rag_store.create_document(kb_version_id=version, created_by=104, source_name='scan.pdf',
        source_type='pdf', content_hash='a'*64)
    job = rag_store.create_job(document_id=original, kb_version_id=version, requested_by=104)
    rag_store.replace_chunks(original, version, [
        {'chunk_kind': 'parent', 'order_no': 1, 'content': 'y=2 X 2+1=5', 'content_hash': 'b'*64}])
    rag_store.update_job(job, status='SUCCEEDED', metrics={'ocr_pages': [1]})
    rag_store.update_document_status(original, status='PROCESSED')
    settings = Settings(_env_file=None, rag_enabled=True, rag_upload_dir=str(tmp_path / 'uploads'))
    index, audit = FakeIndex(), []
    monkeypatch.setattr('edu_core.config.settings.get_settings', lambda: settings)
    monkeypatch.setattr('edu_core.rag.ingestion.RagIngestionService',
        lambda store, s: RagIngestionService(store, s, FakeEmbedding(), index))
    monkeypatch.setattr(teacher, '_stores', lambda: SimpleNamespace(
        rag=rag_store, audit=SimpleNamespace(insert=lambda **kw: audit.append(kw))))
    app = FastAPI()
    app.include_router(teacher.router)
    app.dependency_overrides[teacher.require_teacher] = lambda: {'id': 104}
    with TestClient(app) as client:
        token = rag_store.get_ocr_review(original, created_by=104)['token']
        data = {'token': token, 'text': 'y = 2 × 2 + 1 = 5\n\n| x | 2 |\n| y | 5 |', 'note': '修正乘号'}
        url = f'/teacher/rag/documents/{original}/correction'
        assert client.post(url, json=data | {'token': 'stale'}).status_code == 409
        assert client.post(url, json=data | {'note': ''}).status_code == 400
        assert client.post(url, json=data | {'text': 'x'*500001}).status_code == 400
        app.dependency_overrides[teacher.require_teacher] = lambda: {'id': 999}
        assert client.post(url, json=data).status_code == 404
        assert client.get(f'/teacher/rag/documents/{original}/source').status_code == 404
        app.dependency_overrides[teacher.require_teacher] = lambda: {'id': 104}
        response = client.post(url, json=data)
        assert response.status_code == 200
        corrected = response.json()['document_id']
        assert corrected != original and index.written
        state = rag_store.get_ocr_review(corrected, created_by=104)
        assert state['status'] == 'PENDING' and state['correction_provenance']['source_document_id'] == original
        assert '×' in ''.join(state['parent_texts'])
        assert rag_store.list_document_chunks(original)[0]['content'] == 'y=2 X 2+1=5'
        with pytest.raises(ValueError, match='人工复核'):
            rag_store.set_document_publication(corrected, created_by=104, published=True)
        rag_store.review_ocr_document(corrected, created_by=104, token=state['token'], approved=True, note='逐项核对完成')
        rag_store.set_document_publication(corrected, created_by=104, published=True)
        assert audit[-1]['action'] == 'correct_rag_document'
        downloaded = client.get(f'/teacher/rag/documents/{corrected}/source')
        assert downloaded.status_code == 200 and downloaded.content.decode() == data['text']
