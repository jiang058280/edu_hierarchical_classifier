"""客观题判分引擎测试。"""

import pytest

from edu_core.application.grading import grade_objective, grade_submission, is_objective_type


@pytest.mark.parametrize(
    "answer,reference",
    [
        ("A", "A"),
        ("a.", "A"),
        ("Ａ、", "A"),
        ("选 A", "答案：A"),
        ("AC", "A、C"),
        ("C A", "AC"),
    ],
)
def test_selection_normalization(answer, reference):
    assert grade_objective(answer, reference, "选择题") is True


def test_multiple_choice_compares_as_set():
    assert grade_objective("AB", "ABC", "多选题") is False


def test_empty_student_selection_is_wrong():
    assert grade_objective("", "B", "单选题") is False


@pytest.mark.parametrize("answer", ["正确", "对", "T", "true", "√", "1"])
def test_true_judgment_aliases(answer):
    assert grade_objective(answer, "正确", "判断题") is True


@pytest.mark.parametrize("answer", ["错误", "错", "F", "false", "×", "0"])
def test_false_judgment_aliases(answer):
    assert grade_objective(answer, "错误", "判断题") is True


def test_wrong_judgment_is_false():
    assert grade_objective("对", "错误", "判断题") is False


def test_unsupported_type_raises_in_objective_function():
    with pytest.raises(ValueError, match="不支持自动判分"):
        grade_objective("答案", "答案", "解答题")


def test_empty_reference_raises_in_objective_function():
    with pytest.raises(ValueError, match="参考答案为空"):
        grade_objective("A", "", "选择题")


def test_objective_type_detection():
    assert is_objective_type("选择题") is True
    assert is_objective_type("判断题") is True
    assert is_objective_type("填空题") is False


def test_grade_submission_mixes_objective_subjective_and_missing_reference():
    questions = [
        {"id": 1, "question_type": "选择题", "answer": "B"},
        {"id": 2, "question_type": "判断题", "answer": "正确"},
        {"id": 3, "question_type": "解答题", "answer": "证明略"},
        {"id": 4, "question_type": "选择题", "answer": ""},
    ]
    answers = [
        {"question_id": 1, "answer": "b."},
        {"question_id": 2, "answer": "错"},
        {"question_id": 3, "answer": "学生作答"},
    ]

    assert grade_submission(questions, answers) == [
        {"question_id": 1, "is_correct": True},
        {"question_id": 2, "is_correct": False},
        {"question_id": 3, "is_correct": None},
        {"question_id": 4, "is_correct": None},
    ]


def test_grade_submission_marks_missing_objective_answer_wrong():
    questions = [{"id": 9, "question_type": "选择题", "answer": "D"}]
    assert grade_submission(questions, []) == [{"question_id": 9, "is_correct": False}]


def test_grade_submission_uses_last_duplicate_answer():
    questions = [{"id": 5, "question_type": "判断题", "answer": "正确"}]
    answers = [
        {"question_id": 5, "answer": "错误"},
        {"question_id": 5, "answer": "正确"},
    ]
    assert grade_submission(questions, answers) == [{"question_id": 5, "is_correct": True}]


def test_dirty_reference_becomes_ungraded_in_submission():
    questions = [{"id": 7, "question_type": "判断题", "answer": "不确定"}]
    answers = [{"question_id": 7, "answer": "正确"}]
    assert grade_submission(questions, answers) == [{"question_id": 7, "is_correct": None}]
