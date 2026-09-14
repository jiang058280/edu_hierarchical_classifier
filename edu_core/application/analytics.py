"""R3.6 学生/班级学情展示所需的可复算聚合规则。"""
from __future__ import annotations


def reteach_items(rows: list[dict], *, minimum_attempts: int = 3) -> list[dict]:
    """只把有足够样本且错误率高的知识点列入“需重讲”。"""
    items = []
    for row in rows:
        attempts = int(row.get("attempts") or 0)
        correct_rate = float(row.get("correct_rate") or 0)
        if attempts >= minimum_attempts and correct_rate < .6:
            items.append({**row, "error_rate": round(1 - correct_rate, 4),
                          "evidence": f"{attempts} 次已判分作答，正确率 {round(correct_rate * 100)}%"})
    return sorted(items, key=lambda item: (-item["error_rate"], -int(item["attempts"])))
