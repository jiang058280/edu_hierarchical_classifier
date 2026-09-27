"""AI 智能录入的本地标注整理。

这里不调用任何外部模型：优先提取教师/题库文本中明确写出的元数据，
再以分类模型的结果作为兜底。模型训练内部的 ``学科::知识点`` 也只在
模型边界存在，不能泄漏到教师界面或题库事实数据。
"""

from __future__ import annotations

import re
from typing import Any


_META_MARKER = re.compile(
    r"(?P<key>考查\s*知识点|知识点|参考\s*答案|答案|解析|难度|学段)\s*[:：]",
    re.IGNORECASE,
)


def normalize_knowledge_point(value: str | None, subject: str | None = None) -> str:
    """将内部标签 ``数学::三角函数`` 规范为可展示、可存储的 ``三角函数``。"""
    result = re.sub(r"\s+", " ", str(value or "")).strip(" ：:")
    if not result:
        return ""
    # 训练标签使用 subject::knowledge；允许历史数据有重复前缀。
    while True:
        match = re.match(r"^([^:：]{1,64})\s*[:：]{2,}\s*(.+)$", result)
        if not match:
            break
        result = match.group(2).strip(" ：:")
    if subject:
        prefix = re.escape(subject.strip())
        result = re.sub(rf"^{prefix}\s*[:：]+\s*", "", result).strip(" ：:")
    return result


def _difficulty(value: str) -> int | None:
    matched = re.search(r"(?<!\d)([1-5])(?!\d)", value)
    if matched:
        return int(matched.group(1))
    compact = value.strip()
    if any(word in compact for word in ("容易", "简单", "易")):
        return 1
    if "中等" in compact or compact == "中":
        return 3
    if any(word in compact for word in ("困难", "较难", "难")):
        return 4
    return None


def suggest_difficulty(text: str, question_type: str = "") -> int:
    """给没有明确难度的题目提供透明的本地规则建议（1~5）。"""
    compact = re.sub(r"\s+", "", text)
    score = 2
    if question_type in {"判断题", "填空题", "选择题", "单选题"} and len(compact) <= 80:
        score = 1
    if question_type in {"解答题", "计算题", "证明题", "写作题", "阅读理解题"}:
        score = max(score, 3)
    if any(word in compact for word in ("材料", "阅读下列", "综合", "探究", "证明", "参数", "分类讨论", "多选")):
        score += 1
    if len(compact) > 220 or compact.count("（") + compact.count("(") >= 3:
        score += 1
    return max(1, min(5, score))


def extract_embedded_metadata(text: str, *, subject: str = "") -> dict[str, Any]:
    """提取粘贴题目中显式的知识点、答案、解析、难度、学段。

    支持元数据被换行或横向粘贴压成一行；第一个元数据标记之前才作为题干。
    """
    source = str(text or "").replace("\r", "\n").strip()
    matches = list(_META_MARKER.finditer(source))
    if not matches:
        return {"text": source, "metadata": {}, "sources": {}}
    content = source[:matches[0].start()].strip()
    metadata: dict[str, Any] = {}
    sources: dict[str, str] = {}
    names = {
        "考查知识点": "knowledge_point", "知识点": "knowledge_point",
        "参考答案": "answer", "答案": "answer", "解析": "analysis",
        "难度": "difficulty", "学段": "grade_band",
    }
    for index, marker in enumerate(matches):
        value = source[marker.end(): matches[index + 1].start() if index + 1 < len(matches) else len(source)]
        value = re.sub(r"\s+", " ", value).strip(" ：:")
        field = names[re.sub(r"\s+", "", marker.group("key"))]
        if not value:
            continue
        if field == "knowledge_point":
            value = normalize_knowledge_point(value, subject)
        elif field == "difficulty":
            value = _difficulty(value)
            if value is None:
                continue
        elif field == "grade_band":
            value = "初中" if "初中" in value else "高中" if "高中" in value else value
        metadata[field] = value
        sources[field] = "题目原文"
    return {"text": content or source, "metadata": metadata, "sources": sources}
