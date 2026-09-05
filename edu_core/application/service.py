"""分类应用服务（应用编排层核心）。

职责（对齐 knowforge 的 QAService 设计）：
- 接收 API 层请求，编排 推理 -> 置信度分级 -> 留痕 -> 统计 -> 查重；
- 不写 SQL（全部通过 storage 的 Store），不感知 HTTP；
- 构造函数不保存请求级状态，实例由 factory 进程级单例管理，支持并发。

与旧版 app.py 的差异：
- 分类留痕持久化到 MySQL（旧版内存列表重启即失）；
- 反馈关联 classification_id 与 model_version（旧版反馈无关联、无法再训练）；
- 统计按日累计入库（旧版 stats.json 单日覆盖丢历史）；
- 题库入库时做 Milvus 语义查重（旧版无查重）。
"""

from __future__ import annotations

from typing import Any

from edu_core.application import confidence as conf_mod
from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings
from edu_core.dedup.milvus_client import QuestionDedupIndex
from edu_core.inference.predictor import HierarchicalPredictor
from edu_core.storage.stores import StoreBundle

logger = get_logger(__name__)


class ValidationError(ValueError):
    """请求参数不合法。"""


class ClassificationService:
    """教育题目层级分类应用服务。"""

    def __init__(self, predictor: HierarchicalPredictor, stores: StoreBundle,
                 dedup: QuestionDedupIndex, settings: Settings):
        self.predictor = predictor
        self.stores = stores
        self.dedup = dedup
        self.settings = settings

    # ------------------------------------------------------------------
    # 在线分类主链路
    # ------------------------------------------------------------------
    def classify(self, text: str) -> dict[str, Any]:
        """单条题目三级分类（主链路）。

        流程：输入校验 -> 推理 -> 置信度分级 -> 分类留痕 -> 按日统计。
        返回 payload 兼容旧前端契约（subject/q_type/knowledge/confidences），
        并新增 id/band/review_hint/model_version/duplicates 字段。
        """
        text = (text or "").strip()
        if not text:
            raise ValidationError("题目文本不能为空")
        if len(text) > int(self.settings.classify_max_chars):
            raise ValidationError(f"题目文本超过长度上限 {self.settings.classify_max_chars} 字符")

        result = self.predictor.predict(text)
        avg_conf = conf_mod.average_confidence(result["confidence"])
        band = conf_mod.band(avg_conf, self.settings.confidence_high, self.settings.confidence_medium)

        classification_id = self.stores.classifications.insert(
            text_content=text,
            model_version=result["model_version"],
            result=result,
            avg_confidence=avg_conf,
            band=band,
        )
        self.stores.stats.bump_processed(avg_conf)

        return {
            # 旧前端契约字段
            "subject": result["subject"],
            "q_type": result["question_type"],
            "knowledge": result["knowledge_point"],
            "confidences": result["confidence"],
            # 新增字段
            "id": classification_id,
            "avg_confidence": avg_conf,
            "band": band,
            "review_hint": conf_mod.review_hint(band),
            "model_version": result["model_version"],
            "latency_ms": result.get("latency_ms", 0.0),
            "cached": result.get("cached", False),
        }

    # ------------------------------------------------------------------
    # 反馈闭环
    # ------------------------------------------------------------------
    def submit_feedback(self, is_correct: bool, classification_id: int | None = None,
                        question_text: str | None = None, subject: str | None = None,
                        corrected_subject: str | None = None, corrected_type: str | None = None,
                        corrected_knowledge: str | None = None,
                        comment: str | None = None) -> dict:
        """提交反馈；关联分类留痕，记录模型版本（再训练闭环的数据入口）。"""
        model_version = None
        if classification_id:
            record = self.stores.classifications.get(classification_id)
            if not record:
                raise ValidationError(f"分类记录不存在：{classification_id}")
            model_version = record["model_version"]
            subject = subject or record["subject_pred"]
            question_text = question_text or record["text_preview"]
        feedback_id = self.stores.feedback.insert(
            classification_id=classification_id, is_correct=is_correct,
            question_text=question_text, subject=subject,
            corrected_subject=corrected_subject, corrected_type=corrected_type,
            corrected_knowledge=corrected_knowledge, comment=comment,
            model_version=model_version,
        )
        self.stores.stats.bump_feedback(is_correct)
        counts = self.stores.feedback.counts()
        return {"status": "ok", "id": feedback_id, **counts}

    # ------------------------------------------------------------------
    # 题库
    # ------------------------------------------------------------------
    def save_question(self, text: str, subject: str, question_type: str,
                      knowledge_point: str, source: str = "manual") -> dict:
        """题目入库 + 语义查重（Milvus 可用时返回疑似重复题提示）。"""
        text = (text or "").strip()
        if not text:
            raise ValidationError("题目文本不能为空")
        if len(text) > int(self.settings.classify_max_chars):
            raise ValidationError(f"题目文本超过长度上限 {self.settings.classify_max_chars} 字符")

        question_id = self.stores.questions.insert(
            content=text, subject=subject, question_type=question_type,
            knowledge_point=knowledge_point, source=source)
        indexed = self.dedup.upsert_question(question_id, text)

        duplicates = []
        if indexed:
            hits = self.dedup.search_similar(text, exclude_id=question_id)
            threshold = float(self.settings.dedup_similarity_threshold)
            duplicates = [h for h in hits if h["similarity"] >= threshold]
        return {"status": "ok", "id": question_id, "dedup_indexed": indexed, "duplicates": duplicates}

    def list_questions(self, subject: str = "", question_type: str = "",
                       keyword: str = "", limit: int = 100, offset: int = 0) -> dict:
        items, total = self.stores.questions.list(
            subject=subject, question_type=question_type, keyword=keyword,
            limit=limit, offset=offset)
        return {"total": total, "items": items}

    def delete_question(self, question_id: int) -> dict:
        if not self.stores.questions.delete(question_id):
            raise ValidationError(f"题目不存在：{question_id}")
        self.dedup.delete_question(question_id)
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # 统计 / 历史 / 分析 / 组卷
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """聚合统计（口径兼容旧前端：total_processed/correct/wrong/avg_confidence/question_count/subject_distribution）。"""
        today = self.stores.stats.today()
        counts = self.stores.feedback.counts()
        return {
            "stat_date": today["stat_date"],
            "total_processed": int(today["total_processed"]),
            "total_correct": counts["total_correct"],
            "total_wrong": counts["total_wrong"],
            "avg_confidence": float(today["avg_confidence"]),
            "question_count": self.stores.questions.count(),
            "subject_distribution": self.stores.classifications.subject_distribution(),
        }

    def history(self, limit: int = 20) -> list[dict]:
        return self.stores.classifications.recent(limit=limit)

    def analysis(self) -> dict:
        result = self.stores.feedback.analysis()
        result["feedback_total"] = self.stores.feedback.counts()["total"]
        return result

    def generate_paper(self, subjects: list[str] | None, types: list[str] | None,
                       count: int) -> dict:
        """规则式组卷 MVP：按学科/题型筛选题库，顺序取前 N 题。"""
        if not 1 <= int(count) <= 100:
            raise ValidationError("组卷数量需在 1~100 之间")
        all_items: list[dict] = []
        matched = 0
        subject_list = subjects or [""]
        type_list = types or [""]
        for s in subject_list:
            for t in type_list:
                items, total = self.stores.questions.list(subject=s, question_type=t, limit=1000)
                if s or t:
                    matched += total
                elif total > matched:
                    matched = total
                all_items.extend(items)
        # 去重 + 截断
        seen: set[int] = set()
        unique = [q for q in all_items if not (q["id"] in seen or seen.add(q["id"]))]
        return {"questions": unique[: int(count)], "total_matched": matched}
