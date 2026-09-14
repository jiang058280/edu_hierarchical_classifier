"""RAG 各层共享的数据契约，不绑定具体模型或向量数据库。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkMetadata:
    """所有可检索子块都必须具备的最小来源与访问控制字段。"""

    document_id: int
    parent_chunk_id: int | None
    subject: str | None
    grade_band: str | None
    grade: str | None
    knowledge_node_id: int | None
    chapter: str | None
    source_name: str
    page_number: int | None
    kb_version: str
    status: str
    allowed_roles: tuple[str, ...]
    content_hash: str
    chunk_id: int | None = None


@dataclass(frozen=True)
class RetrievalCandidate:
    """检索层输出，R2 只消费此对象并校验来源。"""

    content: str
    metadata: ChunkMetadata
    score: float
