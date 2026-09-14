"""R2.2：证据约束生成、服务器端引用与可靠拒答。"""

from __future__ import annotations

from dataclasses import dataclass

from edu_core.config.settings import Settings
from edu_core.rag.providers import ChatProvider, OpenAICompatibleChatProvider
from edu_core.rag.retrieval import RagRetrievalService, RetrievalFilters


@dataclass(frozen=True)
class RagAnswer:
    answer: str
    citations: list[dict]
    refused: bool
    reason: str | None = None


class RagAnswerService:
    """只根据检索证据回答；引用由服务器从已验证候选构造，不信任模型自行编造。"""

    def __init__(self, retrieval: RagRetrievalService, settings: Settings,
                 chat_provider: ChatProvider | None = None):
        self.retrieval, self.settings = retrieval, settings
        self.chat_provider = chat_provider or OpenAICompatibleChatProvider(settings)

    def answer(self, query: str, *, role: str, filters: RetrievalFilters,
               learner_context: dict | None = None, socratic: bool = False,
               excluded_question_ids: set[int] | None = None) -> RagAnswer:
        local_answer = self._local_question_answer(query, filters, excluded_question_ids or set())
        if local_answer:
            return local_answer
        debug = self.retrieval.debug(query, role=role, filters=filters)
        evidence = [item for item in debug["candidates"]
                    if float(item["score"]) >= float(self.settings.rag_min_evidence_score)]
        if not evidence:
            return RagAnswer("当前知识库中没有足够可靠的资料来回答这个问题。", [], True,
                             debug.get("reason") or "最高证据分不足")
        context, citations = self._context_and_citations(evidence)
        if not context or not citations:
            return RagAnswer("当前知识库中没有可验证的引用资料，暂不生成回答。", [], True, "引用不可验证")
        profile_note = self._learner_note(learner_context)
        teaching_style = ("采用苏格拉底式辅导：先给一个简短思考问题或下一步提示，"
                          "不要直接给完整答案；学生追问后再逐步展开。" if socratic else
                          "先讲概念，再给可执行的解题或复习步骤。")
        prompt = (
            "你是教育学习助手。只能依据【资料】回答【问题】，不得补充资料外的事实；"
            "表达不确定时要明确说明。用简洁、适合学生的中文解释，不要捏造出处。"
            f"{teaching_style}\n\n【问题】\n{debug['normalized_query']}\n"
            f"{profile_note}\n【资料】\n{context}")
        answer = self.chat_provider.complete([
            {"role": "system", "content": "你必须忠实使用给定资料，证据不足时明确说明。"},
            {"role": "user", "content": prompt},
        ]).strip()
        if not answer:
            return RagAnswer("生成服务未返回有效内容，暂不提供无依据回答。", [], True, "生成内容为空")
        return RagAnswer(answer, citations, False)

    def _local_question_answer(self, query: str, filters: RetrievalFilters,
                               excluded_question_ids: set[int]) -> RagAnswer | None:
        """优先使用本地题库标准答案；该分支不会触发任何外部 Provider。"""
        candidates = self.retrieval.local_question_candidates(query, filters=filters)
        if not candidates:
            return None
        item = next((candidate for candidate in candidates
                     if int(candidate["question_id"]) not in excluded_question_ids), None)
        if not item:
            return None
        if float(item["score"]) < float(self.settings.rag_local_question_min_score):
            return None
        answer, analysis = (item.get("answer") or "").strip(), (item.get("analysis") or "").strip()
        if not answer and not analysis:
            return None
        lines = [f"题库中找到相关题目：{(item.get('question_text') or '').strip()[:220]}"]
        if answer:
            lines.append(f"参考答案：{answer}")
        if analysis:
            lines.append(f"解析：{analysis}")
        citation = {
            "number": 1, "source_name": f"本地题库 · 第 {item['question_id']} 题",
            "chapter": item.get("knowledge_point") or item.get("question_type") or None,
            "page_number": None, "kb_version": "本地题库", "score": item["score"],
        }
        return RagAnswer("\n\n".join(lines), [citation], False, "本地题库直接命中；未调用大模型")

    @staticmethod
    def _learner_note(learner_context: dict | None) -> str:
        if not learner_context or not learner_context.get("weak_points"):
            return ""
        points = []
        for item in learner_context["weak_points"][:3]:
            points.append(f"{item.get('knowledge_point', '未命名知识点')}（掌握度 {round(float(item.get('mastery_score', 0)) * 100)}%，"
                          f"样本 {item.get('attempt_count', 0)} 次）")
        return "【学习摘要（无身份信息，仅用于调整讲解顺序）】\n当前优先关注：" + "；".join(points) + "。\n"

    def _context_and_citations(self, evidence: list[dict]) -> tuple[str, list[dict]]:
        remaining = int(self.settings.rag_max_context_chars)
        parts, citations = [], []
        seen: set[tuple[str, str | None, int | None]] = set()
        for number, item in enumerate(evidence, start=1):
            metadata, content = item["metadata"], item["content"].strip()
            if not content or remaining <= 0:
                continue
            excerpt = content[:remaining]
            remaining -= len(excerpt)
            parts.append(f"[{number}] {excerpt}")
            key = (metadata["source_name"], metadata.get("chapter"), metadata.get("page_number"))
            if key not in seen:
                citations.append({"number": number, "source_name": metadata["source_name"],
                                  "chapter": metadata.get("chapter"), "page_number": metadata.get("page_number"),
                                  "kb_version": metadata["kb_version"], "score": item["score"]})
                seen.add(key)
        return "\n\n".join(parts), citations
