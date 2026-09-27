"""R1.4 资料加载、父子分块与入库编排测试（无外部模型和 Milvus 依赖）。"""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from edu_core.config.settings import Settings
from edu_core.rag.chunking import build_parent_child_chunks
from edu_core.rag.ingestion import RagIngestionService
from edu_core.rag.loaders import DocumentLoadError, load_document_bytes


class FakeStore:
    def __init__(self):
        self.document_id, self.job_id = 10, 20
        self.reusable = None
        self.documents, self.jobs, self.chunks = [], [], []

    def find_reusable_document(self, **_kwargs):
        return self.reusable

    def create_document(self, **kwargs):
        self.documents.append(kwargs | {"id": self.document_id})
        return self.document_id

    def create_job(self, **kwargs):
        self.jobs.append(kwargs | {"id": self.job_id, "status": "QUEUED"})
        return self.job_id

    def update_document_status(self, _document_id, **kwargs):
        self.documents[-1]["status"] = kwargs["status"]
        self.documents[-1]["failure_reason"] = kwargs.get("failure_reason")
        return True

    def update_job(self, _job_id, **kwargs):
        self.jobs[-1].update(kwargs)
        return True

    def replace_chunks(self, document_id, kb_version_id, chunks):
        parents, rows = {}, []
        for index, chunk in enumerate(chunks, start=1):
            parent_id = parents.get(chunk.get("parent_order_no"))
            row = chunk | {"id": index, "document_id": document_id, "kb_version_id": kb_version_id,
                           "parent_chunk_id": parent_id, "status": "STAGED"}
            if chunk["chunk_kind"] == "parent":
                parents[chunk["order_no"]] = index
            rows.append(row)
        self.chunks = rows
        return [row["id"] for row in rows]

    def list_document_chunks(self, _document_id, *, chunk_kind=None):
        return [row for row in self.chunks if row["chunk_kind"] == chunk_kind]


class FakeEmbedding:
    def embed(self, texts):
        return [[float(index), 0.5] for index, _ in enumerate(texts, start=1)]


class FakeIndex:
    def __init__(self, fail=False):
        self.fail, self.written, self.deleted = fail, [], []

    def upsert_chunks(self, chunks, vectors):
        if self.fail:
            raise RuntimeError("索引写入异常")
        self.written.append((chunks, vectors))

    def delete_chunks(self, chunk_ids):
        self.deleted.append(chunk_ids)


def _settings(tmp_path):
    return Settings(_env_file=None, rag_enabled=True, rag_upload_dir=str(tmp_path / "uploads"),
                    rag_embedding_dimension=2, rag_parent_chunk_chars=160,
                    rag_child_chunk_chars=60, rag_chunk_overlap_chars=12)


def test_loader_cleans_markdown_and_extracts_docx():
    loaded = load_document_bytes("讲义.md", b"# \xe4\xb8\x80\xe6\xac\xa1\xe5\x87\xbd\xe6\x95\xb0\r\n\r\n  y = kx + b\x00\r\n")
    assert loaded.source_type == "markdown"
    assert "一次函数" in loaded.text and "\x00" not in loaded.text

    document = Document()
    document.add_paragraph("DOCX 中的函数概念")
    stream = BytesIO()
    document.save(stream)
    assert "函数概念" in load_document_bytes("资料.docx", stream.getvalue()).text


def test_loader_rejects_unsupported_or_empty_file():
    with pytest.raises(DocumentLoadError, match="仅支持"):
        load_document_bytes("资料.xlsx", b"not a document")
    with pytest.raises(DocumentLoadError, match="为空"):
        load_document_bytes("资料.txt", b"")


def test_correction_is_a_new_unreviewed_draft_with_provenance(tmp_path):
    store, index = FakeStore(), FakeIndex()
    store.reusable = {'id': 999}
    result = RagIngestionService(store, _settings(tmp_path), FakeEmbedding(), index).ingest(
        kb_version_id=1, created_by=7, source_name='校对.txt', content='y = 2 × 2 + 1 = 5'.encode(),
        allow_reuse=False, correction_provenance={'source_document_id': 23, 'note': '修正乘号'})
    assert result.document_id != 999
    assert store.jobs[-1]['metrics']['requires_review'] is True
    assert store.jobs[-1]['metrics']['correction_provenance']['source_document_id'] == 23
    assert store.documents[-1]['status'] == 'PROCESSED'
    assert index.written and all(c['status'] == 'STAGED' for c in store.chunks)


def test_parent_child_chunks_are_bounded_and_linkable():
    text = "# 第一章 一次函数\n\n" + "一次函数的图像和性质。" * 35
    chunks = build_parent_child_chunks(text, parent_chars=160, child_chars=60, overlap_chars=10)
    parents = [item for item in chunks if item.chunk_kind == "parent"]
    children = [item for item in chunks if item.chunk_kind == "child"]
    assert len(parents) >= 2 and children
    assert all(len(item.content) <= 160 for item in parents)
    assert {item.parent_order_no for item in children} <= {item.order_no for item in parents}
    assert len({item.order_no for item in children}) == len(children)


def test_ingestion_writes_storage_chunks_vectors_and_status(tmp_path):
    store, index = FakeStore(), FakeIndex()
    service = RagIngestionService(store, _settings(tmp_path), FakeEmbedding(), index)
    result = service.ingest(kb_version_id=1, created_by=7, source_name="函数.md",
                            content=("# 函数\n\n" + "一次函数知识。" * 30).encode(), subject="数学", grade="初三")
    assert result.chunk_count > 0 and not result.reused
    assert store.documents[-1]["status"] == "PROCESSED"
    assert store.jobs[-1]["status"] == "SUCCEEDED"
    assert index.written and all(row["parent_chunk_id"] for row in store.list_document_chunks(10, chunk_kind="child"))
    assert (tmp_path / "uploads" / "7").exists()


def test_ingestion_marks_single_job_failed_and_cleans_vectors(tmp_path):
    store, index = FakeStore(), FakeIndex(fail=True)
    service = RagIngestionService(store, _settings(tmp_path), FakeEmbedding(), index)
    with pytest.raises(RuntimeError, match="索引写入"):
        service.ingest(kb_version_id=1, created_by=7, source_name="函数.txt", content=("函数知识。" * 40).encode())
    assert store.documents[-1]["status"] == "FAILED"
    assert store.jobs[-1]["status"] == "FAILED"
    assert index.deleted


def test_ingestion_records_parse_failure_against_its_own_job(tmp_path):
    store = FakeStore()
    service = RagIngestionService(store, _settings(tmp_path), FakeEmbedding(), FakeIndex())
    with pytest.raises(DocumentLoadError, match="未从资料"):
        service.ingest(kb_version_id=1, created_by=7, source_name="空资料.txt", content=b" \r\n")
    assert store.documents[-1]["status"] == "FAILED"
    assert store.jobs[-1]["status"] == "FAILED"


def test_ingestion_reuses_processed_document_without_writing(tmp_path):
    store, index = FakeStore(), FakeIndex()
    store.reusable = {"id": 99}
    service = RagIngestionService(store, _settings(tmp_path), FakeEmbedding(), index)
    result = service.ingest(kb_version_id=1, created_by=7, source_name="函数.txt", content=b"same")
    assert result == result.__class__(document_id=99, job_id=None, chunk_count=0, reused=True)
    assert not store.documents and not index.written
