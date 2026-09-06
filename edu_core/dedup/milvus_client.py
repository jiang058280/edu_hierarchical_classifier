"""Milvus 题目语义查重（增强组件，可降级）。

职责：
- 题目文本经 BERT [CLS] 池化向量（768 维）写入 Milvus collection（COSINE 相似度）；
- 录入题目时 top-K 相似检索，相似度 >= 阈值提示疑似重复题。

边界（明确区别于业务主库）：
- 业务数据（题库/反馈/版本）在 MySQL，Milvus 只存向量与最小元数据；
- Milvus 不可用时本模块返回 available=False，业务链路跳过查重继续工作
  （这是增强功能的可选降级，与"核心链路不允许降级"的原则不冲突）。
"""

from __future__ import annotations

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings

logger = get_logger(__name__)


class QuestionDedupIndex:
    """Milvus 题目向量索引（懒连接、失败降级）。"""

    def __init__(self, settings: Settings, embed_fn=None):
        self.settings = settings
        self.embed_fn = embed_fn  # predictor.embed
        self._client = None
        self._unavailable_reason: str | None = None

    # ---------- 连接与集合 ----------
    def _ensure_client(self):
        if self._unavailable_reason:
            return None
        if self._client is not None:
            return self._client
        if not self.settings.milvus_enabled:
            self._unavailable_reason = "milvus_disabled"
            return None
        try:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=self.settings.milvus_uri)
            # 已存在的集合直接复用（参数不一致的旧集合也优先兼容，避免反复建集合报错）
            if not client.has_collection(self.settings.milvus_collection):
                client.create_collection(
                    collection_name=self.settings.milvus_collection,
                    dimension=int(self.settings.dedup_vector_dim),
                    metric_type="COSINE",
                    auto_id=False,
                )
            self._client = client
            logger.info("Milvus 查重集合就绪：%s@%s",
                        self.settings.milvus_collection, self.settings.milvus_uri)
            return client
        except Exception as exc:  # noqa: BLE001 — 查重是增强功能，任何失败都降级
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("Milvus 不可用，查重功能降级跳过：%s", self._unavailable_reason)
            return None

    def available(self) -> bool:
        """查重索引是否可用。"""
        return self._ensure_client() is not None

    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    # ---------- 写入 ----------
    def upsert_question(self, question_id: int, text: str) -> bool:
        """题目向量入库（question_id 为主键，重复录入覆盖旧向量）。"""
        client = self._ensure_client()
        if client is None or self.embed_fn is None:
            return False
        try:
            vector = self.embed_fn(text)
            client.upsert(
                collection_name=self.settings.milvus_collection,
                data=[{"id": int(question_id), "vector": vector}],
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("题目向量入库失败（跳过）：%s", exc)
            return False

    def delete_question(self, question_id: int) -> None:
        client = self._ensure_client()
        if client is None:
            return
        try:
            client.delete(collection_name=self.settings.milvus_collection,
                          ids=[int(question_id)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("题目向量删除失败（跳过）：%s", exc)

    # ---------- 检索 ----------
    def search_similar(self, text: str, top_k: int | None = None,
                       exclude_id: int | None = None) -> list[dict]:
        """返回相似题目 [{question_id, similarity}]；索引不可用返回空列表。"""
        client = self._ensure_client()
        if client is None or self.embed_fn is None:
            return []
        top_k = top_k or int(self.settings.dedup_top_k)
        try:
            vector = self.embed_fn(text)
            results = client.search(
                collection_name=self.settings.milvus_collection,
                data=[vector],
                limit=top_k + (1 if exclude_id else 0),
                output_fields=["id"],
            )
            hits = []
            for hit in (results[0] if results else []):
                qid = int(hit.get("id") or hit.get("pk") or 0)
                if exclude_id and qid == int(exclude_id):
                    continue
                # pymilvus MilvusClient search 返回 distance；COSINE 下 distance 即相似度
                similarity = float(hit.get("distance", 0.0))
                hits.append({"question_id": qid, "similarity": round(similarity, 4)})
            return hits[:top_k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("相似题目检索失败（跳过）：%s", exc)
            return []
