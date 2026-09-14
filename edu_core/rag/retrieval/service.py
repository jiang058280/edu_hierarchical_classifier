"""R2.1：不调用 LLM 的教育知识库检索与调试编排。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Protocol

from edu_core.config.settings import Settings
from edu_core.rag.contracts import ChunkMetadata, RetrievalCandidate
from edu_core.rag.indexing import RagIndexError
from edu_core.rag.providers import EmbeddingProvider, OpenAICompatibleEmbeddingProvider
from edu_core.storage.stores import RagKnowledgeBaseStore


@dataclass(frozen=True)
class RetrievalFilters:
    subject: str | None = None
    grade_band: str | None = None
    grade: str | None = None
    knowledge_node_id: int | None = None


class VectorRetriever(Protocol):
    def search(self, vector: list[float], *, limit: int) -> list[dict]: ...


class MilvusCollectionRetriever:
    """仅返回主键和相似分，最终内容必须由 MySQL 再次授权过滤。"""

    def __init__(self, settings: Settings, collection: str):
        self.settings, self.collection, self._client = settings, collection, None

    def _client_or_raise(self):
        if not self.settings.milvus_enabled:
            raise RagIndexError("Milvus 未启用")
        if self._client is None:
            try:
                from pymilvus import MilvusClient

                self._client = MilvusClient(uri=self.settings.milvus_uri)
                if not self._client.has_collection(self.collection):
                    raise RagIndexError(f"检索集合不存在：{self.collection}")
            except RagIndexError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise RagIndexError("向量检索服务不可用") from exc
        return self._client

    def search(self, vector: list[float], *, limit: int) -> list[dict]:
        results = self._client_or_raise().search(collection_name=self.collection, data=[vector], limit=limit)
        return [{"id": int(hit.get("id") or hit.get("pk")), "score": float(hit.get("distance", 0.0))}
                for hit in (results[0] if results else [])]


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query or "").strip()


def query_variants(query: str) -> list[str]:
    normalized = normalize_query(query)
    stripped = re.sub(r"^(请问|请解释|帮我讲讲|什么是|如何理解)", "", normalized).strip("？?，,。.")
    return list(dict.fromkeys(item for item in (normalized, stripped) if item))


def _local_terms(text: str) -> set[str]:
    """无需模型的中英文检索特征：中文二元词 + 英文/数字词。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    terms = {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}
    terms.update(token for token in re.findall(r"[a-z0-9_+.=-]{2,}", normalized))
    return terms


def route_intent(query: str) -> str:
    return "faq_preferred" if re.search(r"(是什么|什么意思|定义|概念)$", query) else "document_question"


class RagRetrievalService:
    def __init__(self, store: RagKnowledgeBaseStore, settings: Settings,
                 embedding_provider: EmbeddingProvider | None = None,
                 document_retriever: VectorRetriever | None = None):
        self.store, self.settings = store, settings
        self.embedding_provider = embedding_provider or OpenAICompatibleEmbeddingProvider(settings)
        self.document_retriever = document_retriever or MilvusCollectionRetriever(settings, settings.rag_documents_collection)

    def local_question_candidates(self, query: str, *, filters: RetrievalFilters) -> list[dict]:
        """本地题库知识源检索，不访问 Embedding、LLM 或 Milvus。"""
        normalized = normalize_query(query)
        query_terms = _local_terms(normalized)
        if not normalized or not query_terms:
            return []
        candidates = []
        for item in self.store.list_local_question_knowledge(
                subject=filters.subject, grade_band=filters.grade_band, grade=filters.grade):
            question = normalize_query(item.get("question_text") or "")
            haystack = " ".join(str(item.get(key) or "") for key in
                                 ("question_text", "answer", "analysis", "knowledge_point", "question_type"))
            haystack_terms = _local_terms(haystack)
            overlap = len(query_terms & haystack_terms) / len(query_terms)
            # 作业错题上下文会携带完整题干；该情形可可靠地直接定位标准答案。
            exact = len(question) >= 12 and question in normalized
            score = 1.0 if exact else overlap
            if score >= 0.55:
                candidates.append(item | {"score": round(score, 4), "exact": exact})
        return sorted(candidates, key=lambda item: (item["score"], item["question_id"]), reverse=True)[:3]

    def debug(self, query: str, *, role: str, filters: RetrievalFilters) -> dict:
        normalized = normalize_query(query)
        if not normalized:
            raise ValueError("检索问题不能为空")
        if not self.settings.rag_enabled:
            raise ValueError("RAG 当前未启用")
        active = self.store.get_active_version()
        base = {"normalized_query": normalized, "variants": query_variants(normalized),
                "intent": route_intent(normalized), "filters": asdict(filters),
                "active_version": active.get("version") if active else None, "candidates": []}
        if not active:
            return base | {"reason": "当前没有已激活知识库版本"}
        vectors = self.embedding_provider.embed(base["variants"])
        raw_scores: dict[int, float] = {}
        for vector in vectors:
            for hit in self.document_retriever.search(vector, limit=int(self.settings.rag_top_k)):
                raw_scores[hit["id"]] = max(raw_scores.get(hit["id"], float("-inf")), hit["score"])
        chunks = self.store.get_retrieval_chunks(list(raw_scores), kb_version_id=int(active["id"]), role=role,
                                                 subject=filters.subject, grade_band=filters.grade_band,
                                                 grade=filters.grade, knowledge_node_id=filters.knowledge_node_id)
        candidates = []
        for chunk in chunks:
            metadata = ChunkMetadata(document_id=int(chunk["document_id"]), parent_chunk_id=chunk.get("parent_chunk_id"),
                subject=chunk.get("subject"), grade_band=chunk.get("grade_band"), grade=chunk.get("grade"),
                knowledge_node_id=chunk.get("knowledge_node_id"), chapter=chunk.get("chapter"),
                source_name=chunk["source_name"], page_number=chunk.get("page_number"), kb_version=chunk["kb_version"],
                status=chunk["status"], allowed_roles=tuple(chunk.get("allowed_roles") or ()),
                content_hash=chunk["content_hash"], chunk_id=int(chunk["id"]))
            candidates.append(RetrievalCandidate(chunk["content"], metadata, raw_scores[int(chunk["id"])]))
        candidates.sort(key=lambda item: item.score, reverse=True)
        return base | {"candidates": [{"chunk_id": item.metadata.chunk_id,
                                         "content": item.content, "score": round(item.score, 4),
                                         "metadata": asdict(item.metadata)} for item in candidates[:int(self.settings.rag_rerank_top_k)]],
                       "reason": None if candidates else "没有满足版本、发布状态和权限过滤的证据"}
