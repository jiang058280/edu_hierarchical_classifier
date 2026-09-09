"""统一题型目录：为题库、AI 确认、组卷和学生作答提供同一口径。"""

from __future__ import annotations

SUBJECTS = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治")

# answer_mode 决定学生端控件；model_coarse 是当前三分类模型可表达的粗粒度标签。
TYPE_META = {
    "选择题": ("choice_single", True, "选择题"),
    "单选题": ("choice_single", True, "选择题"),
    "多选题": ("choice_multiple", True, "选择题"),
    "判断题": ("judgment", True, "判断题"),
    "填空题": ("text", False, "解答题"),
    "解答题": ("text", False, "解答题"),
    "简答题": ("text", False, "解答题"),
    "计算题": ("text", False, "解答题"),
    "证明题": ("text", False, "解答题"),
    "实验探究题": ("text", False, "解答题"),
    "作图题": ("text", False, "解答题"),
    "化学方程式题": ("text", False, "解答题"),
    "推断题": ("text", False, "解答题"),
    "材料分析题": ("text", False, "解答题"),
    "识图题": ("text", False, "解答题"),
    "读图题": ("text", False, "解答题"),
    "论述题": ("text", False, "解答题"),
    "阅读理解题": ("text", False, "解答题"),
    "文言文阅读题": ("text", False, "解答题"),
    "语言文字运用题": ("text", False, "解答题"),
    "完形填空题": ("text", False, "解答题"),
    "语法填空题": ("text", False, "解答题"),
    "写作题": ("text", False, "解答题"),
}

COMMON = ("选择题", "单选题", "多选题", "判断题", "填空题", "解答题")
TYPES_BY_SUBJECT = {
    "数学": COMMON + ("计算题", "证明题"),
    "语文": COMMON + ("简答题", "阅读理解题", "文言文阅读题", "语言文字运用题", "写作题"),
    "英语": COMMON + ("完形填空题", "阅读理解题", "语法填空题", "写作题"),
    "物理": COMMON + ("计算题", "实验探究题", "作图题"),
    "化学": COMMON + ("计算题", "实验探究题", "化学方程式题", "推断题"),
    "生物": COMMON + ("简答题", "实验探究题", "材料分析题", "识图题"),
    "历史": COMMON + ("简答题", "材料分析题", "论述题"),
    "地理": COMMON + ("简答题", "材料分析题", "读图题", "计算题"),
    "政治": COMMON + ("简答题", "材料分析题", "论述题"),
}


def taxonomy_payload(subject: str = "", counts: dict[str, int] | None = None) -> dict:
    """生成前端可直接消费的题型目录，可附带当前题库数量。"""
    if subject and subject not in TYPES_BY_SUBJECT:
        raise ValueError(f"不支持的学科：{subject}")
    names = TYPES_BY_SUBJECT[subject] if subject else tuple(TYPE_META)
    count_map = counts or {}
    return {
        "subjects": list(SUBJECTS),
        "subject": subject or None,
        "model_question_types": ["选择题", "判断题", "解答题"],
        "types": [
            {
                "name": name,
                "answer_mode": TYPE_META[name][0],
                "objective": TYPE_META[name][1],
                "model_coarse": TYPE_META[name][2],
                "available_count": int(count_map.get(name, 0)),
            }
            for name in names
        ],
        "types_by_subject": {key: list(value) for key, value in TYPES_BY_SUBJECT.items()},
    }


def is_type_allowed(subject: str, question_type: str) -> bool:
    return subject in TYPES_BY_SUBJECT and question_type in TYPES_BY_SUBJECT[subject]
