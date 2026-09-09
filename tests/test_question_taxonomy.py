"""跨学科题型目录与组卷库存反馈测试。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from edu_core.api import teacher
from edu_core.application.question_taxonomy import (
    SUBJECTS,
    TYPES_BY_SUBJECT,
    is_type_allowed,
    taxonomy_payload,
)


def test_all_nine_subjects_have_deduplicated_question_types():
    assert len(SUBJECTS) == 9
    assert set(SUBJECTS) == set(TYPES_BY_SUBJECT)
    for subject, names in TYPES_BY_SUBJECT.items():
        assert len(names) == len(set(names)), subject
        assert {"选择题", "判断题", "填空题", "解答题"}.issubset(names)


def test_subject_specific_types_and_answer_modes():
    assert is_type_allowed("数学", "证明题")
    assert not is_type_allowed("英语", "证明题")
    assert is_type_allowed("英语", "完形填空题")
    payload = taxonomy_payload("物理", {"选择题": 12, "实验探究题": 3})
    by_name = {item["name"]: item for item in payload["types"]}
    assert by_name["选择题"] == {
        "name": "选择题", "answer_mode": "choice_single", "objective": True,
        "model_coarse": "选择题", "available_count": 12,
    }
    assert by_name["实验探究题"]["available_count"] == 3
    assert by_name["实验探究题"]["model_coarse"] == "解答题"


def test_unknown_subject_rejected():
    with pytest.raises(ValueError, match="不支持的学科"):
        taxonomy_payload("体育")


class FakeQuestions:
    def question_type_counts(self, **kwargs):
        assert kwargs["subject"] == "数学"
        return {"选择题": 2, "解答题": 1}

    def list(self, **kwargs):
        available = {"选择题": [{"id": 1}, {"id": 2}], "解答题": [{"id": 3}]}
        items = available.get(kwargs["question_type"], [])
        return items, len(items)


def test_taxonomy_api_includes_inventory(monkeypatch):
    monkeypatch.setattr(
        teacher, "_stores", lambda: SimpleNamespace(questions=FakeQuestions()))
    result = teacher.question_taxonomy("数学", "高中", "", None, {})
    counts = {item["name"]: item["available_count"] for item in result["types"]}
    assert counts["选择题"] == 2
    assert counts["证明题"] == 0


def test_generate_paper_reports_shortage(monkeypatch):
    monkeypatch.setattr(
        teacher, "_stores", lambda: SimpleNamespace(questions=FakeQuestions()))
    result = teacher.generate_paper({
        "subject": "数学", "type_counts": {"选择题": 4, "解答题": 1}}, {})
    assert result["total"] == 3
    assert result["shortages"] == [
        {"question_type": "选择题", "requested": 4, "available": 2}]


def test_generate_rejects_cross_subject_type(monkeypatch):
    monkeypatch.setattr(
        teacher, "_stores", lambda: SimpleNamespace(questions=FakeQuestions()))
    with pytest.raises(HTTPException, match="不支持题型") as exc:
        teacher.generate_paper({
            "subject": "英语", "type_counts": {"证明题": 1}}, {})
    assert exc.value.status_code == 400
