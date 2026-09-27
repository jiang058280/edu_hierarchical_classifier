"""R3.3 可解释候选重排与语义召回增强。"""
from __future__ import annotations

import re


def score_candidate(profile: dict, question: dict, seen_ids: set[int]) -> tuple[float, list[str]]:
    weak = 1 - float(profile['mastery_score'])
    exact = 1.0 if question.get('knowledge_point') == profile['knowledge_point'] else .35
    target = int(profile['recommended_difficulty'])
    diff = question.get('difficulty') or target
    fit = max(0, 1 - abs(int(diff) - target) / 4)
    novelty = 0.0 if int(question['id']) in seen_ids else 1.0
    score = round(.45 * weak + .25 * exact + .15 * fit + .15 * novelty, 4)
    reasons = [f"掌握度 {round(profile['mastery_score'] * 100)}%", "同知识点" if exact == 1 else "同学科关联", f"难度 {diff} 适配"]
    if not novelty:
        reasons.append("近期已练，已降权")
    return score, reasons


def score_candidate_semantic(base_score: float, reasons: list[str], similarity: float, *,
                             low: float, high: float, weight: float) -> tuple[float, list[str]]:
    """在既有 base 分之上叠加带内相似度加成，总分上限 1.0；理由追加语义依据。"""
    band = min(1.0, max(0.0, (similarity - low) / max(high - low, 1e-9)))
    score = min(1.0, round(float(base_score) + weight * band, 4))
    return score, reasons + [f"与错题语义相关 {round(similarity * 100)}%"]


class SemanticRecaller:
    """错题为种子 → 查重向量集合的语义近邻；卡带过滤；索引不可用时映射为空。

    相似度达 high 视为近似重复题直接排除（不推荐给学生的"另一道原题"），
    低于 low 视为噪声不计分；两者之间才是"相似但值得练"的有效带。
    """

    def __init__(self, dedup, settings):
        self.dedup = dedup
        self.settings = settings

    def similarity_map(self, seed_texts: list[str], exclude_ids: set[int]) -> dict[int, dict]:
        settings = self.settings
        top_k = int(settings.recommend_semantic_top_k)
        low = float(settings.recommend_semantic_low)
        high = float(settings.recommend_semantic_high)
        mapping: dict[int, dict] = {}
        for seed in seed_texts[:int(settings.recommend_semantic_seeds)]:
            seed = str(seed or "").strip()
            if not seed:
                continue
            for hit in self.dedup.search_similar(seed, top_k=top_k) or []:
                try:
                    question_id = int(hit.get("question_id") or 0)
                    similarity = float(hit.get("similarity") or 0.0)
                except (TypeError, ValueError):
                    continue
                if question_id in exclude_ids or not low <= similarity < high:
                    continue
                current = mapping.get(question_id)
                if current is None or similarity > current["similarity"]:
                    mapping[question_id] = {"similarity": similarity, "seed": seed[:40]}
        return mapping


def clean_seed_text(content: str) -> str:
    """错题原文含选项与"28."式粘贴序号，截断为题干主体供向量化。"""
    text = str(content or "").strip()
    text = re.sub(r"^\s*\d{1,3}[.．、)]\s*", "", text)
    return text[:200]
