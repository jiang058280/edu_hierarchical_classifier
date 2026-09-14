"""生成 100 条 RAG 人工标注模板；生成后须关联真实资料分块才可设为 ready=true。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "eval_sets" / "rag_baseline.jsonl"

SCENARIOS = {
    "answerable": ("请根据教材资料解释{topic}。", False),
    "unanswerable": ("知识库资料中没有{topic}时，请直接说明不能回答。", True),
    "cross_grade": ("我目前处于{grade_band}，请不要使用不属于本学段资料来讲解{topic}。", True),
    "wrong_question": ("错题复盘：我在{topic}上出错，请说明应查阅哪部分资料。", False),
    "follow_up": ("连续追问：基于前一轮的{topic}讲解，请再说明一个易混淆点。", False),
}
TOPICS = ["一次函数图像", "二次函数判别式", "光合作用条件", "细胞分裂", "英语时态", "文言实词", "牛顿运动定律", "化学方程式配平", "地理气候类型", "历史事件因果"]


def main() -> int:
    cases = []
    categories = list(SCENARIOS)
    for number in range(100):
        category = categories[number % len(categories)]
        template, expect_refusal = SCENARIOS[category]
        topic = TOPICS[number % len(TOPICS)]
        grade_band = "初中" if number % 2 == 0 else "高中"
        cases.append({
            "id": f"RAG-{number + 1:03d}", "category": category,
            "query": template.format(topic=topic, grade_band=grade_band), "role": "student",
            "filters": {"grade_band": grade_band}, "expect_refusal": expect_refusal,
            "expected_chunk_ids": [], "ready": False,
            "annotation_note": "请由教师关联已发布资料的 child chunk ID，并复核应答/拒答预期后再设为 true。",
        })
    OUTPUT.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n", encoding="utf-8")
    print(f"Generated {len(cases)} RAG evaluation annotation templates: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
