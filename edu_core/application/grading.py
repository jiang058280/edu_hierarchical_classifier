"""客观题判分规则。

本模块只包含纯函数，不访问数据库、不感知 HTTP，便于在作业提交、错题重练和
自主练习中复用同一套判分口径。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


_SELECTION_TYPES = {"选择题", "单选题", "多选题"}
_JUDGMENT_TYPES = {"判断题"}

_TRUE_ANSWERS = {"正确", "对", "是", "t", "true", "yes", "y", "1", "√", "✓"}
_FALSE_ANSWERS = {"错误", "错", "否", "f", "false", "no", "n", "0", "×", "✕", "x"}

_OPTION_PATTERN = re.compile(r"[A-Z]")
_IGNORABLE_OPTION_WORDS = re.compile(r"(?:答案|选择|选项|应选|故选|正确选项|为|是)", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[\s.。．、,，:：;；()（）\[\]【】{}]+")


def _normalize_text(value: Any) -> str:
    """统一全半角、大小写和首尾空白。"""
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().lower()


def _normalize_question_type(question_type: Any) -> str:
    return unicodedata.normalize("NFKC", str(question_type or "")).strip()


def _selection_options(value: Any) -> frozenset[str]:
    """把 ``A.``, ``选 A、C`` 和全角字母等形式归一为选项集合。"""
    normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
    normalized = _IGNORABLE_OPTION_WORDS.sub("", normalized)
    normalized = _PUNCTUATION.sub("", normalized)
    return frozenset(_OPTION_PATTERN.findall(normalized))


def _judgment_value(value: Any) -> bool | None:
    normalized = _normalize_text(value)
    normalized = _PUNCTUATION.sub("", normalized)
    if normalized in _TRUE_ANSWERS:
        return True
    if normalized in _FALSE_ANSWERS:
        return False
    return None


def is_objective_type(question_type: str) -> bool:
    """判断题型是否可由规则自动判分。"""
    normalized = _normalize_question_type(question_type)
    return normalized in _SELECTION_TYPES | _JUDGMENT_TYPES


def grade_objective(answer: str, reference: str, question_type: str) -> bool:
    """判定一题客观题是否正确。

    ``reference`` 为空或题型不是当前支持的客观题时抛出 ``ValueError``，避免调用方
    把“无法判分”误写成答错。作业级函数 :func:`grade_submission` 会把这些情况映射
    为 ``is_correct=None``。
    """
    qtype = _normalize_question_type(question_type)
    if qtype not in _SELECTION_TYPES | _JUDGMENT_TYPES:
        raise ValueError(f"不支持自动判分的题型：{question_type}")
    if not _normalize_text(reference):
        raise ValueError("参考答案为空，无法自动判分")

    if qtype in _SELECTION_TYPES:
        expected = _selection_options(reference)
        actual = _selection_options(answer)
        if not expected:
            raise ValueError("参考答案中未识别到选项")
        return bool(actual) and actual == expected

    expected_judgment = _judgment_value(reference)
    if expected_judgment is None:
        raise ValueError("参考答案中未识别到判断结果")
    actual_judgment = _judgment_value(answer)
    return actual_judgment is not None and actual_judgment == expected_judgment


def grade_submission(
    questions: Iterable[Mapping[str, Any]],
    answers: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按题目顺序判分，返回 ``[{question_id, is_correct}]``。

    - 选择题、判断题且参考答案有效：返回 ``True`` 或 ``False``；
    - 主观题、未知题型、参考答案为空：返回 ``None``；
    - 学生未提交某道可判客观题：按空答案处理，返回 ``False``；
    - 同一题出现多份答案时采用最后一份，匹配表单更新后再提交的常见语义。
    """
    answer_by_question: dict[Any, Any] = {}
    for item in answers:
        if "question_id" not in item:
            continue
        answer_by_question[item["question_id"]] = item.get("answer")

    results: list[dict[str, Any]] = []
    for question in questions:
        question_id = question.get("id", question.get("question_id"))
        question_type = _normalize_question_type(question.get("question_type"))
        reference = question.get("answer")
        is_correct: bool | None = None

        if is_objective_type(question_type) and _normalize_text(reference):
            student_answer = answer_by_question.get(question_id, "")
            try:
                is_correct = grade_objective(student_answer, reference, question_type)
            except ValueError:
                # 脏参考答案不能计作学生答错，交给教师复核。
                is_correct = None

        results.append({"question_id": question_id, "is_correct": is_correct})
    return results
