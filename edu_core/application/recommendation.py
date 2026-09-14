"""R3.3 可解释候选重排。"""
from __future__ import annotations

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
