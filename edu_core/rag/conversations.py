"""R2.3：学生 RAG 会话编排与审计。

会话只保存问题、答案及服务器生成的引用；学生档案不会进入提示词或持久化的检索上下文。
"""

from __future__ import annotations

from time import perf_counter

from edu_core.rag.generation import RagAnswer, RagAnswerService
from edu_core.rag.retrieval import RetrievalFilters
from edu_core.storage.stores import RagKnowledgeBaseStore


class RagConversationService:
    def __init__(self, store: RagKnowledgeBaseStore, answer_service: RagAnswerService):
        self.store, self.answer_service = store, answer_service

    def ask(self, *, user_id: int, query: str, session_id: int | None,
            filters: RetrievalFilters, learner_context: dict | None = None,
            learning_action: str | None = None, socratic: bool = False,
            excluded_question_ids: set[int] | None = None) -> dict:
        query = " ".join((query or "").split())
        if not query:
            raise ValueError("问题不能为空")
        if len(query) > 2000:
            raise ValueError("问题不能超过 2000 个字符")
        if session_id is None:
            session_id = self.store.create_session(user_id, "student", query[:64])
        elif not self.store.get_owned_session(session_id, user_id, "student"):
            raise ValueError("会话不存在或无权访问")

        user_message_id = self.store.add_message(session_id, "user", query)
        started = perf_counter()
        if learning_action:
            result = self._profile_answer(learning_action, learner_context or {})
        else:
            answer_kwargs = {}
            if learner_context:
                answer_kwargs["learner_context"] = learner_context
            if socratic:
                answer_kwargs["socratic"] = True
            if excluded_question_ids:
                answer_kwargs["excluded_question_ids"] = excluded_question_ids
            result = self.answer_service.answer(query, role="student", filters=filters, **answer_kwargs)
        latency_ms = int((perf_counter() - started) * 1000)
        assistant_message_id = self.store.add_message(
            session_id, "assistant", result.answer, citations=result.citations, refused=result.refused)
        kb_version = result.citations[0].get("kb_version") if result.citations else None
        self.store.add_query_trace(
            session_id=session_id, user_id=user_id, kb_version=kb_version, query_text=query,
            candidates=[{"source_name": item.get("source_name"), "score": item.get("score")}
                        for item in result.citations], latency_ms=latency_ms,
            failure_stage="evidence" if result.refused else None)
        return {"session_id": session_id, "user_message_id": user_message_id,
                "message_id": assistant_message_id, "answer": result.answer,
                "citations": result.citations, "refused": result.refused, "reason": result.reason,
                "latency_ms": latency_ms}

    @staticmethod
    def _profile_answer(action: str, context: dict) -> RagAnswer:
        points = context.get("weak_points") or []
        citation = [{"number": 1, "source_name": "你的学习记录（系统统计）", "chapter": "掌握度画像",
                     "page_number": None, "kb_version": None, "score": 1.0}]
        if not points:
            return RagAnswer("目前没有足够的已判分作答记录，暂时无法判断薄弱点。先完成几道带知识点标签的练习后，我再为你分析。",
                             citation, False)
        first = points[0]
        label = first.get("knowledge_point") or "该知识点"
        score = round(float(first.get("mastery_score", 0)) * 100)
        attempts = int(first.get("attempt_count", 0))
        if action == "why_recommended":
            answer = (f"我优先推荐“{label}”相关题目，因为当前掌握度约为 {score}%（基于 {attempts} 次已判分作答）。"
                      "题单会优先选择同知识点、难度适配且近期未重复练过的题目。")
        elif action == "study_priority":
            answer = (f"建议先学习“{label}”：它是当前掌握度较低的知识点（约 {score}%）。"
                      "先完成 2～3 道由易到难的专项题，再回看错误原因；完成后系统会按新作答刷新排序。")
        else:
            rows = "；".join(f"{item.get('knowledge_point') or '未标注'} {round(float(item.get('mastery_score', 0))*100)}%（{item.get('attempt_count', 0)} 次）"
                             for item in points)
            answer = f"当前需要重点关注的知识点：{rows}。掌握度来自已判分作答、近期表现和重练记录；样本较少时应把它视为初步信号。"
        return RagAnswer(answer, citation, False)
