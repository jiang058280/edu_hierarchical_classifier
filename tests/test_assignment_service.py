"""AssignmentService 纯业务测试，不依赖 MySQL。"""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from edu_core.application.assignment_service import (
    AssignmentConflictError,
    AssignmentNotFoundError,
    AssignmentPermissionError,
    AssignmentService,
    AssignmentValidationError,
)


NOW = datetime(2026, 9, 8, 12, 0, 0)


class FakeAssignments:
    def __init__(self, papers, classes):
        self.papers = papers
        self.classes = classes
        self.rows = {
            100: {
                "id": 100,
                "paper_id": 10,
                "class_id": 20,
                "title": "函数练习",
                "mode": "homework",
                "due_at": NOW + timedelta(days=1),
                "allow_self_check": True,
                "created_by": 1,
                "questions": deepcopy(papers[10]["questions"]),
                "submission": None,
                "answers": [],
            }
        }
        self.submissions = {
            501: {"id": 501, "assignment_id": 100, "student_id": 2,
                  "status": "submitted", "created_by": 1}
        }
        self.last_submit = None

    def create(self, **fields):
        assignment_id = max(self.rows) + 1
        self.rows[assignment_id] = {
            "id": assignment_id,
            **fields,
            "questions": deepcopy(self.papers[fields["paper_id"]]["questions"]),
            "submission": None,
            "answers": [],
        }
        return assignment_id

    def get_detail(self, assignment_id, student_id=None):
        row = self.rows.get(assignment_id)
        if not row:
            return None
        detail = deepcopy(row)
        if student_id is not None and (detail.get("submission") or {}).get("student_id", 2) != student_id:
            detail["submission"] = None
            detail["answers"] = []
        return detail

    def list_by_teacher(self, teacher_id, limit=50, offset=0):
        items = [deepcopy(r) for r in self.rows.values() if r["created_by"] == teacher_id]
        return items[offset:offset + limit], len(items)

    def list_by_student(self, student_id):
        if student_id == 4:
            return []
        return [{"id": 100, "status": "not_started"}]

    def list_submissions(self, assignment_id):
        if assignment_id != 100:
            return []
        return [
            {"student_id": 2, "status": "submitted", "submission_id": 501,
             "answers": [{"question_id": 101, "is_correct": True}]},
            {"student_id": 6, "status": "not_started", "submission_id": None,
             "answers": []},
        ]

    def submit(self, **fields):
        self.last_submit = deepcopy(fields)
        return {"submission_id": 600, "status": "submitted", "auto_score": 50.0}

    def get_submission(self, submission_id):
        row = self.submissions.get(submission_id)
        return deepcopy(row) if row else None

    def check(self, submission_id, final_score):
        row = self.submissions.get(submission_id)
        if not row or row["status"] not in ("submitted", "checked"):
            return False
        row.update(status="checked", final_score=final_score)
        return True


class FakeLookupStore:
    def __init__(self, rows):
        self.rows = rows

    def get(self, row_id):
        row = self.rows.get(row_id)
        return deepcopy(row) if row else None


@pytest.fixture
def service():
    papers = {
        10: {
            "id": 10,
            "created_by": 1,
            "questions": [
                {"id": 101, "question_type": "选择题", "answer": "A", "analysis": "解析1"},
                {"id": 102, "question_type": "判断题", "answer": "错误", "analysis": "解析2"},
                {"id": 103, "question_type": "解答题", "answer": "过程略", "analysis": "解析3"},
            ],
        },
        11: {"id": 11, "created_by": 9, "questions": [{"id": 201}]},
        12: {"id": 12, "created_by": 1, "questions": []},
    }
    classes = {
        20: {"id": 20, "created_by": 1, "is_active": True},
        21: {"id": 21, "created_by": 9, "is_active": True},
        22: {"id": 22, "created_by": 1, "is_active": False},
    }
    users = {
        2: {"id": 2, "role": "student", "is_active": True, "class_id": 20},
        3: {"id": 3, "role": "student", "is_active": True, "class_id": 21},
        4: {"id": 4, "role": "student", "is_active": True, "class_id": None},
        5: {"id": 5, "role": "teacher", "is_active": True, "class_id": None},
    }
    stores = SimpleNamespace(
        papers=FakeLookupStore(papers),
        classes=FakeLookupStore(classes),
        users=FakeLookupStore(users),
        assignments=FakeAssignments(papers, classes),
    )
    return AssignmentService(stores, now_fn=lambda: NOW)


def test_create_assignment_success(service):
    result = service.create_assignment(
        paper_id=10, class_id=20, title="  周末练习  ", created_by=1,
        due_at=NOW + timedelta(days=2))
    assert result["title"] == "周末练习"
    assert result["questions"][0]["id"] == 101


@pytest.mark.parametrize("title", ["", "  ", "x" * 129])
def test_create_rejects_invalid_title(service, title):
    with pytest.raises(AssignmentValidationError):
        service.create_assignment(paper_id=10, class_id=20, title=title, created_by=1)


def test_create_rejects_invalid_mode(service):
    with pytest.raises(AssignmentValidationError, match="homework/exam"):
        service.create_assignment(paper_id=10, class_id=20, title="练习", created_by=1, mode="quiz")


def test_create_rejects_missing_paper(service):
    with pytest.raises(AssignmentNotFoundError):
        service.create_assignment(paper_id=404, class_id=20, title="练习", created_by=1)


def test_create_rejects_foreign_paper(service):
    with pytest.raises(AssignmentPermissionError):
        service.create_assignment(paper_id=11, class_id=20, title="练习", created_by=1)


def test_create_rejects_empty_paper(service):
    with pytest.raises(AssignmentValidationError, match="空试卷"):
        service.create_assignment(paper_id=12, class_id=20, title="练习", created_by=1)


@pytest.mark.parametrize("class_id,error_type", [(404, AssignmentNotFoundError),
                                                  (22, AssignmentNotFoundError),
                                                  (21, AssignmentPermissionError)])
def test_create_validates_class_ownership_and_state(service, class_id, error_type):
    with pytest.raises(error_type):
        service.create_assignment(paper_id=10, class_id=class_id, title="练习", created_by=1)


def test_create_rejects_past_due_time(service):
    with pytest.raises(AssignmentValidationError, match="晚于当前时间"):
        service.create_assignment(
            paper_id=10, class_id=20, title="练习", created_by=1,
            due_at=NOW - timedelta(seconds=1))


def test_teacher_list_and_ownership(service):
    assert service.list_for_teacher(1)["total"] == 1
    with pytest.raises(AssignmentPermissionError):
        service.get_for_teacher(100, teacher_id=9)


def test_teacher_submission_roster_includes_progress_summary(service):
    result = service.list_submissions(100, teacher_id=1)
    assert result["total"] == 2
    assert result["completed"] == 1
    assert result["assignment"]["title"] == "函数练习"
    assert result["students"][1]["status"] == "not_started"
    assert result["students"][0]["answers"][0]["is_correct"] is True


def test_teacher_cannot_view_foreign_submission_roster(service):
    with pytest.raises(AssignmentPermissionError):
        service.list_submissions(100, teacher_id=9)


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (10, -1)])
def test_teacher_list_validates_pagination(service, limit, offset):
    with pytest.raises(AssignmentValidationError):
        service.list_for_teacher(1, limit=limit, offset=offset)


def test_student_must_exist_and_be_active_but_can_list_before_joining(service):
    with pytest.raises(AssignmentNotFoundError):
        service.list_for_student(999)
    assert service.list_for_student(4) == []
    with pytest.raises(AssignmentNotFoundError):
        service.list_for_student(5)
    service.stores.users.rows[2]["is_active"] = False
    with pytest.raises(AssignmentNotFoundError):
        service.list_for_student(2)


def test_student_detail_hides_reference_answer_and_analysis(service):
    detail = service.get_for_student(100, 2)
    assert "answer" not in detail["questions"][0]
    assert "analysis" not in detail["questions"][0]
    assert "answer" in service.stores.assignments.rows[100]["questions"][0]
    assert datetime.fromisoformat(detail["server_time"]).replace(tzinfo=None) == NOW


def test_student_result_requires_submission_and_reveals_feedback(service):
    with pytest.raises(AssignmentConflictError, match="尚未提交"):
        service.get_result(100, 2)
    row = service.stores.assignments.rows[100]
    row["submission"] = {
        "id": 501, "status": "submitted", "auto_score": 50.0, "final_score": None}
    row["answers"] = [
        {"question_id": 101, "answer": "A", "is_correct": True},
        {"question_id": 103, "answer": "过程", "is_correct": None},
    ]
    result = service.get_result(100, 2)
    assert result["submission"]["auto_score"] == 50.0
    assert result["results"][0]["answer"] == "A"
    assert result["results"][0]["student_answer"] == "A"
    assert result["results"][0]["self_assessed"] is False
    assert result["results"][2]["is_correct"] is None
    assert result["results"][2]["self_assessed"] is False


def test_student_cannot_access_other_class_assignment(service):
    with pytest.raises(AssignmentPermissionError):
        service.get_for_student(100, 3)


def test_submit_grades_all_questions_and_preserves_subjective_ungraded(service):
    result = service.submit(100, 2, [
        {"question_id": 101, "answer": "a."},
        {"question_id": 102, "answer": "正确"},
        {"question_id": 103, "answer": "计算过程"},
    ])
    assert result["status"] == "submitted"
    assert service.stores.assignments.last_submit["answers"] == [
        {"question_id": 101, "answer": "a.", "is_correct": True, "auto_gradable": True},
        {"question_id": 102, "answer": "正确", "is_correct": False, "auto_gradable": True},
        {"question_id": 103, "answer": "计算过程", "is_correct": None, "auto_gradable": False},
    ]


def test_submit_supports_subjective_self_check(service):
    service.submit(100, 2, [{"question_id": 103, "answer": "过程", "self_check": True}])
    records = service.stores.assignments.last_submit["answers"]
    assert records[2]["is_correct"] is True
    assert records[2]["auto_gradable"] is False


def test_submit_rejects_self_check_when_disabled(service):
    service.stores.assignments.rows[100]["allow_self_check"] = False
    with pytest.raises(AssignmentValidationError, match="不允许主观题自评"):
        service.submit(100, 2, [{"question_id": 103, "answer": "过程", "self_check": True}])


def test_submit_rejects_foreign_question_and_long_answer(service):
    with pytest.raises(AssignmentValidationError, match="不属于该作业"):
        service.submit(100, 2, [{"question_id": 999, "answer": "A"}])
    with pytest.raises(AssignmentValidationError, match="512"):
        service.submit(100, 2, [{"question_id": 101, "answer": "x" * 513}])


@pytest.mark.parametrize("delta", [timedelta(seconds=-1), timedelta()])
def test_submit_rejects_at_or_after_deadline(service, delta):
    service.stores.assignments.rows[100]["due_at"] = NOW + delta
    with pytest.raises(AssignmentConflictError, match="已截止"):
        service.submit(100, 2, [])


def test_submit_rejects_duplicate_submission(service):
    service.stores.assignments.rows[100]["submission"] = {"status": "submitted"}
    with pytest.raises(AssignmentConflictError, match="重复提交"):
        service.submit(100, 2, [])


def test_check_submission_success(service):
    result = service.check_submission(501, teacher_id=1, final_score=88.5)
    assert result == {"submission_id": 501, "status": "checked", "final_score": 88.5}


@pytest.mark.parametrize("score", [-1, 101, "bad", None, float("nan"), float("inf")])
def test_check_rejects_invalid_score(service, score):
    with pytest.raises(AssignmentValidationError):
        service.check_submission(501, teacher_id=1, final_score=score)


def test_check_rejects_missing_or_foreign_submission(service):
    with pytest.raises(AssignmentNotFoundError):
        service.check_submission(404, teacher_id=1, final_score=80)
    with pytest.raises(AssignmentPermissionError):
        service.check_submission(501, teacher_id=9, final_score=80)


def test_check_rejects_unsubmitted_state(service):
    service.stores.assignments.submissions[501]["status"] = "in_progress"
    with pytest.raises(AssignmentConflictError, match="尚未提交"):
        service.check_submission(501, teacher_id=1, final_score=80)


@pytest.mark.parametrize("enabled", ["false", 0, 1, None])
def test_create_requires_boolean_self_check_setting(service, enabled):
    with pytest.raises(AssignmentValidationError, match="布尔值"):
        service.create_assignment(
            paper_id=10, class_id=20, title="练习", created_by=1,
            allow_self_check=enabled)


def test_create_normalizes_offset_deadline_to_server_local_time(service):
    due_at = (NOW + timedelta(days=1)).astimezone()
    detail = service.create_assignment(
        paper_id=10, class_id=20, title="练习", created_by=1,
        due_at=due_at.isoformat())
    assert detail["due_at"] == due_at.replace(tzinfo=None)


@pytest.mark.parametrize("due_at", ["bad-date", NOW])
def test_create_rejects_invalid_or_equal_deadline(service, due_at):
    with pytest.raises(AssignmentValidationError):
        service.create_assignment(
            paper_id=10, class_id=20, title="练习", created_by=1, due_at=due_at)


@pytest.mark.parametrize("new_class", [None, 21])
@pytest.mark.parametrize("status", ["submitted", "checked"])
def test_student_retains_own_submitted_history_after_leaving_class(service, new_class, status):
    service.stores.users.rows[2]["class_id"] = new_class
    service.stores.assignments.rows[100]["submission"] = {
        "id": 501, "student_id": 2, "status": status}
    assert service.get_for_student(100, 2)["submission"]["id"] == 501
    assert service.get_result(100, 2)["results"][0]["answer"] == "A"
    with pytest.raises(AssignmentPermissionError):
        service.get_for_student(100, 3)


@pytest.mark.parametrize("status", [None, "in_progress"])
def test_student_without_class_cannot_open_unsubmitted_assignment(service, status):
    service.stores.assignments.rows[100]["submission"] = {
        "student_id": 4, "status": status}
    with pytest.raises(AssignmentPermissionError):
        service.get_for_student(100, 4)
    with pytest.raises(AssignmentValidationError, match="尚未加入班级"):
        service.submit(100, 4, [])


@pytest.mark.parametrize("correct", [True, False])
def test_result_marks_subjective_self_assessment_explicitly(service, correct):
    row = service.stores.assignments.rows[100]
    row["submission"] = {"id": 501, "student_id": 2, "status": "submitted"}
    row["answers"] = [{"question_id": 103, "answer": "过程", "is_correct": correct}]
    result = service.get_result(100, 2)["results"][2]
    assert result["self_assessed"] is True
    assert result["is_correct"] is correct


@pytest.mark.parametrize("question_id", [True, False, None, "101.0", 101.0, 101.5, [], {}])
def test_submit_rejects_non_integer_question_ids(service, question_id):
    with pytest.raises(AssignmentValidationError, match="整数"):
        service.submit(100, 2, [{"question_id": question_id, "answer": "A"}])


def test_submit_accepts_string_id_and_records_unanswered_questions(service):
    service.submit(100, 2, [{"question_id": "101", "answer": "A"}])
    records = service.stores.assignments.last_submit["answers"]
    assert len(records) == 3
    assert records[0]["is_correct"] is True
    assert records[1]["answer"] is None
    assert records[1]["is_correct"] is False
    assert records[2]["is_correct"] is None


@pytest.mark.parametrize("answer", [True, 1, [], {}])
def test_submit_rejects_non_string_answers(service, answer):
    with pytest.raises(AssignmentValidationError, match="字符串或 null"):
        service.submit(100, 2, [{"question_id": 101, "answer": answer}])


@pytest.mark.parametrize("answers", [None, {}, "A", [None], [{}]])
def test_submit_rejects_malformed_answer_payload(service, answers):
    with pytest.raises(AssignmentValidationError):
        service.submit(100, 2, answers)


@pytest.mark.parametrize("self_check", ["true", 0, 1, []])
def test_submit_rejects_non_boolean_subjective_self_check(service, self_check):
    with pytest.raises(AssignmentValidationError, match="布尔值"):
        service.submit(100, 2, [{"question_id": 103, "self_check": self_check}])


def test_submit_rejects_paper_with_no_remaining_questions(service):
    service.stores.assignments.rows[100]["questions"] = []
    with pytest.raises(AssignmentConflictError, match="无可用题目"):
        service.submit(100, 2, [])


def test_submit_translates_storage_race_to_conflict(service, monkeypatch):
    def concurrent_submit(**kwargs):
        raise ValueError("该作业已经提交，不能重复提交")

    monkeypatch.setattr(service.stores.assignments, "submit", concurrent_submit)
    with pytest.raises(AssignmentConflictError, match="重复提交"):
        service.submit(100, 2, [])


def test_check_detects_storage_state_change(service, monkeypatch):
    monkeypatch.setattr(service.stores.assignments, "check", lambda *args: False)
    with pytest.raises(AssignmentConflictError, match="状态已变化"):
        service.check_submission(501, teacher_id=1, final_score=80)
