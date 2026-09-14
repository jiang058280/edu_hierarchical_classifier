"""R1.6 向量最小 metadata 契约：R2 检索必须可据此执行硬过滤。"""

from __future__ import annotations

import pytest

from edu_core.config.settings import Settings
from edu_core.rag.indexing import MilvusRagDocumentIndex, RagIndexError


class FakeMilvus:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


def test_document_index_writes_required_filter_metadata():
    settings = Settings(_env_file=None, milvus_enabled=True, rag_embedding_dimension=2)
    index, client = MilvusRagDocumentIndex(settings), FakeMilvus()
    index._client = client
    chunk = {
        "id": 31, "document_id": 7, "kb_version_id": 3, "parent_chunk_id": 30,
        "content_hash": "a" * 64, "chapter": "函数", "status": "PUBLISHED",
        "metadata": {"source_name": "函数讲义.md", "subject": "数学", "grade_band": "初中",
                     "grade": "初三", "knowledge_node_id": 9, "allowed_roles": ["student"]},
    }
    index.upsert_chunks([chunk], [[0.1, 0.2]])
    row = client.upserts[0]["data"][0]
    assert {"document_id", "kb_version_id", "parent_chunk_id", "source_name", "subject", "grade_band",
            "grade", "knowledge_node_id", "allowed_roles", "status", "content_hash"} <= row.keys()
    assert row["subject"] == "数学" and row["allowed_roles"] == ["student"]


def test_document_index_rejects_incorrect_embedding_dimension():
    settings = Settings(_env_file=None, milvus_enabled=True, rag_embedding_dimension=2)
    with pytest.raises(RagIndexError, match="维度"):
        MilvusRagDocumentIndex(settings).upsert_chunks([{"id": 1}], [[0.1]])
