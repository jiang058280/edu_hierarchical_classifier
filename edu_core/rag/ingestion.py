"""教师资料的本地保存、解析、父子分块和向量入库编排。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path

from edu_core.config.settings import Settings
from edu_core.rag.chunking import build_parent_child_chunks
from edu_core.rag.indexing.milvus_index import MilvusRagDocumentIndex, VectorIndex
from edu_core.rag.loaders import load_document_bytes, supported_source_type
from edu_core.rag.providers import EmbeddingProvider, OpenAICompatibleEmbeddingProvider
from edu_core.storage.stores import RagKnowledgeBaseStore


@dataclass(frozen=True)
class IngestionResult:
    document_id: int
    job_id: int | None
    chunk_count: int
    reused: bool = False


class RagIngestionService:
    """R1.4 资料管线；单个任务异常不会影响作业主链路。"""

    def __init__(self, store: RagKnowledgeBaseStore, settings: Settings,
                 embedding_provider: EmbeddingProvider | None = None, vector_index: VectorIndex | None = None):
        self.store, self.settings = store, settings
        self.embedding_provider = embedding_provider or OpenAICompatibleEmbeddingProvider(settings)
        self.vector_index = vector_index or MilvusRagDocumentIndex(settings)

    def ingest(self, *, kb_version_id: int, created_by: int, source_name: str, content: bytes,
               subject: str | None = None, grade_band: str | None = None, grade: str | None = None,
               knowledge_node_id: int | None = None, allowed_roles: list[str] | None = None,
               allow_reuse: bool = True) -> IngestionResult:
        if not self.settings.rag_enabled:
            raise ValueError("RAG 当前未启用，不能执行资料入库")
        if len(content) > int(self.settings.rag_max_upload_bytes):
            raise ValueError("资料超过上传大小限制")
        source_type = supported_source_type(source_name)
        content_hash = hashlib.sha256(content).hexdigest()
        if allow_reuse and (reusable := self.store.find_reusable_document(
                created_by=created_by, content_hash=content_hash)):
            return IngestionResult(int(reusable["id"]), None, 0, reused=True)
        storage_key = self._save_upload(created_by, content_hash, source_name, content)
        document_id = self.store.create_document(
            kb_version_id=kb_version_id, created_by=created_by, source_name=source_name, source_type=source_type,
            content_hash=content_hash, storage_key=storage_key, file_size=len(content), subject=subject,
            grade_band=grade_band, grade=grade, knowledge_node_id=knowledge_node_id, allowed_roles=allowed_roles)
        job_id = self.store.create_job(document_id=document_id, kb_version_id=kb_version_id, requested_by=created_by)
        stored_chunk_ids: list[int] = []
        try:
            self.store.update_document_status(document_id, status="PROCESSING")
            self.store.update_job(job_id, status="RUNNING", attempt_no=1)
            loaded = load_document_bytes(source_name, content)
            drafts = build_parent_child_chunks(loaded.text, parent_chars=int(self.settings.rag_parent_chunk_chars),
                                               child_chars=int(self.settings.rag_child_chunk_chars),
                                               overlap_chars=int(self.settings.rag_chunk_overlap_chars))
            metadata = {"source_name": source_name, "subject": subject, "grade_band": grade_band,
                        "grade": grade, "knowledge_node_id": knowledge_node_id,
                        "allowed_roles": allowed_roles or ["student", "teacher", "admin"]}
            stored_chunk_ids = self.store.replace_chunks(
                document_id, kb_version_id, [asdict(draft) | {"metadata": metadata} for draft in drafts])
            child_chunks = self.store.list_document_chunks(document_id, chunk_kind="child")
            self.vector_index.upsert_chunks(child_chunks, self.embedding_provider.embed([item["content"] for item in child_chunks]))
            self.store.update_document_status(document_id, status="PROCESSED")
            self.store.update_job(job_id, status="SUCCEEDED", metrics={"parent_chunks": sum(d.chunk_kind == "parent" for d in drafts), "child_chunks": len(child_chunks), "page_count": loaded.page_count})
            return IngestionResult(document_id, job_id, len(child_chunks))
        except Exception as exc:
            if stored_chunk_ids:
                self.vector_index.delete_chunks(stored_chunk_ids)
            message = str(exc).strip()[:900] or "资料入库失败"
            self.store.update_document_status(document_id, status="FAILED", failure_reason=message)
            self.store.update_job(job_id, status="FAILED", error_code=type(exc).__name__, error_message=message)
            raise

    def _save_upload(self, created_by: int, content_hash: str, source_name: str, content: bytes) -> str:
        relative = Path(self.settings.rag_upload_dir) / str(int(created_by)) / f"{content_hash}{Path(source_name).suffix.lower()}"
        destination = self.settings.abs_path(str(relative))
        upload_root = self.settings.abs_path(self.settings.rag_upload_dir).resolve()
        if upload_root not in destination.resolve().parents:
            raise ValueError("资料存储路径不合法")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(content)
        return relative.as_posix()
