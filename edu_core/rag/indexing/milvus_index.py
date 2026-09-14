"""RAG 文档向量索引；MySQL 状态是事实源，Milvus 不可用即显式失败。"""

from __future__ import annotations

from typing import Protocol

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)


class RagIndexError(RuntimeError):
    """向量索引无法完成。"""


class VectorIndex(Protocol):
    def upsert_chunks(self, chunks: list[dict], vectors: list[list[float]]) -> None: ...
    def delete_chunks(self, chunk_ids: list[int]) -> None: ...


class MilvusRagDocumentIndex:
    """仅写入子块向量与最小检索 metadata，禁止写入学生数据。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    def _ensure_client(self):
        if not self.settings.milvus_enabled:
            raise RagIndexError("Milvus 未启用")
        if self._client is not None:
            return self._client
        try:
            from pymilvus import DataType, MilvusClient

            client = MilvusClient(uri=self.settings.milvus_uri)
            collection = self.settings.rag_documents_collection
            if not client.has_collection(collection):
                schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
                schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
                schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR,
                                 dim=int(self.settings.rag_embedding_dimension))
                indexes = client.prepare_index_params()
                indexes.add_index(field_name="id", index_type="STL_SORT")
                indexes.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
                client.create_collection(collection_name=collection, schema=schema, index_params=indexes)
            self._client = client
            return client
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG 文档向量索引不可用：%s", type(exc).__name__)
            raise RagIndexError("向量索引服务不可用") from exc

    def upsert_chunks(self, chunks: list[dict], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise RagIndexError("待写入的分块与向量数量不一致")
        dimension = int(self.settings.rag_embedding_dimension)
        if any(len(vector) != dimension for vector in vectors):
            raise RagIndexError("Embedding 向量维度与知识库配置不一致")
        data = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            metadata = chunk.get("metadata") or {}
            data.append({"id": int(chunk["id"]), "vector": vector,
                         "document_id": int(chunk["document_id"]), "kb_version_id": int(chunk["kb_version_id"]),
                         "parent_chunk_id": chunk.get("parent_chunk_id"), "source_name": metadata.get("source_name", ""),
                         "subject": metadata.get("subject"), "grade_band": metadata.get("grade_band"),
                         "grade": metadata.get("grade"), "knowledge_node_id": metadata.get("knowledge_node_id"),
                         "chapter": chunk.get("chapter"), "allowed_roles": metadata.get("allowed_roles", []),
                         "status": chunk.get("status", "STAGED"), "content_hash": chunk["content_hash"]})
        try:
            self._ensure_client().upsert(collection_name=self.settings.rag_documents_collection, data=data)
        except RagIndexError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG 子块向量写入失败：%s", type(exc).__name__)
            raise RagIndexError("向量写入失败") from exc

    def delete_chunks(self, chunk_ids: list[int]) -> None:
        if not chunk_ids:
            return
        try:
            self._ensure_client().delete(collection_name=self.settings.rag_documents_collection,
                                         ids=[int(chunk_id) for chunk_id in chunk_ids])
        except Exception as exc:  # noqa: BLE001
            logger.warning("RAG 向量补偿清理失败：%s", type(exc).__name__)
