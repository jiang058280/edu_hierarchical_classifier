"""R3.4 三类巩固任务的题单编排规则。"""
from __future__ import annotations


def ordered_by_difficulty(items: list[dict], target: int | None = None) -> list[dict]:
    """同一知识点先易后难；未知难度放在建议难度附近。"""
    fallback = target or 3
    return sorted(items, key=lambda item: (int(item.get("difficulty") or fallback), int(item["id"])))


def unique_ids(*groups: list[int], limit: int) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for group in groups:
        for question_id in group:
            if question_id not in seen:
                result.append(question_id)
                seen.add(question_id)
            if len(result) >= limit:
                return result
    return result
