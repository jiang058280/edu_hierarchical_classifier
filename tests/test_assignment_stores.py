"""M2 Store SQL 协议回归：假连接验证事务次序，不连接或改动真实数据库。"""

from contextlib import contextmanager
from dataclasses import dataclass
import json

import pytest

from edu_core.storage.stores import AssignmentStore, PaperStore, QuestionStore


@dataclass
class FakeResult:
    row: object = None
    lastrowid: int = 42
    rowcount: int = 1

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.row or []


class ScriptedEngine:
    """按指定 SQL 次序返回结果；失败语句必须触发事务回滚。"""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def begin(self):
        try:
            yield self
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    @contextmanager
    def connect(self):
        yield self

    def execute(self, statement, params):
        sql = " ".join(str(statement).split())
        expected, result = self.steps.pop(0)
        assert expected in sql, sql
        self.calls.append((sql, dict(params)))
        return result


def test_question_insert_preserves_draft_status():
    engine = ScriptedEngine([("INSERT INTO questions", FakeResult())])
    assert QuestionStore(engine).insert("测试题", "数学", status="draft") == 42
    assert "text_hash, status)" in engine.calls[0][0]
    assert engine.calls[0][1]["status"] == "draft"
    assert engine.committed


@pytest.mark.parametrize("options", [["甲", "乙"], [], None])
def test_question_options_can_be_replaced_or_cleared(options):
    engine = ScriptedEngine([
        ("SELECT id FROM questions", FakeResult(row={"id": 5})),
        ("SELECT paper_id FROM paper_questions", FakeResult()),
        ("UPDATE questions SET options_json", FakeResult()),
    ])
    assert QuestionStore(engine).update(5, options=options)
    params = engine.calls[-1][1]
    assert params["options_json"] == (json.dumps(options, ensure_ascii=False) if options else None)
    assert "options" not in params
    assert engine.committed


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_question_referenced_by_paper_is_immutable(operation):
    engine = ScriptedEngine([
        ("SELECT id FROM questions", FakeResult(row={"id": 5})),
        ("SELECT paper_id FROM paper_questions", FakeResult(row={"paper_id": 6})),
    ])
    store = QuestionStore(engine)
    with pytest.raises(ValueError, match="已被试卷使用"):
        if operation == "update":
            store.update(5, content="变更后的题目")
        else:
            store.delete(5)
    assert "FOR UPDATE" in engine.calls[0][0]
    assert len(engine.calls) == 2
    assert engine.rolled_back and not engine.committed


def test_paper_create_locks_sorted_questions_but_preserves_requested_order():
    engine = ScriptedEngine([
        ("SELECT id FROM questions", FakeResult(row={"id": 3})),
        ("SELECT id FROM questions", FakeResult(row={"id": 9})),
        ("INSERT INTO papers", FakeResult()),
        ("INSERT INTO paper_questions", FakeResult()),
        ("INSERT INTO paper_questions", FakeResult()),
    ])
    assert PaperStore(engine).create("测试卷", "数学", "初中", 1, [9, 3, 9]) == 42
    assert [params["i"] for _, params in engine.calls[:2]] == [3, 9]
    assert all("FOR UPDATE" in sql for sql, _ in engine.calls[:2])
    assert [params["q"] for _, params in engine.calls[3:]] == [9, 3]
    assert [params["o"] for _, params in engine.calls[3:]] == [1, 2]


def test_paper_create_rejects_deleted_question_before_any_insert():
    engine = ScriptedEngine([("SELECT id FROM questions", FakeResult())])
    with pytest.raises(ValueError, match="题目不存在"):
        PaperStore(engine).create("测试卷", "数学", "初中", 1, [5])
    assert len(engine.calls) == 1
    assert engine.rolled_back


def test_published_paper_cannot_be_deleted():
    engine = ScriptedEngine([
        ("SELECT id FROM papers", FakeResult(row={"id": 6})),
        ("SELECT id FROM assignments", FakeResult(row={"id": 7})),
    ])
    with pytest.raises(ValueError, match="已发布为作业"):
        PaperStore(engine).delete(6)
    assert "FOR UPDATE" in engine.calls[0][0]
    assert engine.rolled_back


def test_unpublished_paper_can_be_deleted_with_its_question_links():
    engine = ScriptedEngine([
        ("SELECT id FROM papers", FakeResult(row={"id": 6})),
        ("SELECT id FROM assignments", FakeResult()),
        ("DELETE FROM paper_questions", FakeResult()),
        ("DELETE FROM papers", FakeResult()),
    ])
    assert PaperStore(engine).delete(6)
    assert engine.committed


@pytest.mark.parametrize("paper_exists", [True, False])
def test_assignment_create_locks_source_paper(paper_exists):
    steps = [("SELECT id FROM papers", FakeResult(row={"id": 6} if paper_exists else None))]
    if paper_exists:
        steps.append(("INSERT INTO assignments", FakeResult()))
    engine = ScriptedEngine(steps)
    store = AssignmentStore(engine)
    fields = dict(paper_id=6, class_id=2, title="测试作业", created_by=1)
    if paper_exists:
        assert store.create(**fields) == 42
        assert engine.committed
    else:
        with pytest.raises(ValueError, match="试卷不存在"):
            store.create(**fields)
        assert engine.rolled_back
    assert "FOR UPDATE" in engine.calls[0][0]


def submission_engine(answers, *, status="in_progress", allowed=True):
    steps = [
        ("INSERT INTO submissions", FakeResult()),
        ("SELECT id, status FROM submissions", FakeResult(row={"id": 42, "status": status})),
    ]
    if status not in ("submitted", "checked"):
        steps.append(("SELECT a.id FROM assignments", FakeResult(row={"id": 7} if allowed else None)))
        if allowed:
            steps.append(("DELETE FROM answer_records", FakeResult()))
            steps.extend(("INSERT INTO answer_records", FakeResult()) for _ in answers)
            steps.append(("UPDATE submissions", FakeResult()))
    return ScriptedEngine(steps)


def test_submission_uses_upsert_before_lock_and_excludes_subjective_self_scores():
    answers = [
        {"question_id": 1, "answer": "A", "is_correct": True, "auto_gradable": True},
        {"question_id": 2, "answer": "错误", "is_correct": False, "auto_gradable": True},
        {"question_id": 3, "answer": "过程", "is_correct": True, "auto_gradable": False},
        {"question_id": 4, "answer": None, "is_correct": None, "auto_gradable": False},
    ]
    engine = submission_engine(answers)
    result = AssignmentStore(engine).submit(assignment_id=7, student_id=2, answers=answers)
    assert result == {"submission_id": 42, "status": "submitted", "auto_score": 50.0}
    assert "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)" in engine.calls[0][0]
    assert "FOR UPDATE" in engine.calls[1][0]
    assert "a.due_at > :now" in engine.calls[2][0]
    assert engine.calls[2][1]["now"] == engine.calls[-1][1]["now"]
    assert engine.calls[-1][1]["score"] == 50.0
    answer_params = [params for sql, params in engine.calls if "INSERT INTO answer_records" in sql]
    assert [params["correct"] for params in answer_params] == [1, 0, 1, None]
    assert engine.committed and not engine.steps


@pytest.mark.parametrize("answers", [
    [{"question_id": 3, "is_correct": True, "auto_gradable": False}],
    [{"question_id": 1, "is_correct": None, "auto_gradable": True}],
])
def test_submission_without_gradable_objectives_has_no_auto_score(answers):
    engine = submission_engine(answers)
    assert AssignmentStore(engine).submit(assignment_id=7, student_id=2, answers=answers)["auto_score"] is None
    assert engine.calls[-1][1]["score"] is None


@pytest.mark.parametrize("status", ["submitted", "checked"])
def test_duplicate_submission_rolls_back_without_touching_answers(status):
    engine = submission_engine([], status=status)
    with pytest.raises(ValueError, match="重复提交"):
        AssignmentStore(engine).submit(assignment_id=7, student_id=2, answers=[])
    assert len(engine.calls) == 2
    assert engine.rolled_back and not engine.committed


def test_deadline_or_class_change_after_service_validation_rolls_back():
    engine = submission_engine([], allowed=False)
    with pytest.raises(ValueError, match="已截止或学生班级已变化"):
        AssignmentStore(engine).submit(assignment_id=7, student_id=2, answers=[])
    assert len(engine.calls) == 3
    assert engine.rolled_back and not engine.committed


def test_student_assignment_list_preserves_only_own_submitted_history():
    engine = ScriptedEngine([("FROM users u", FakeResult())])
    assert AssignmentStore(engine).list_by_student(2) == []
    sql, params = engine.calls[0]
    assert "history.student_id = u.id" in sql
    assert "history.status IN ('submitted', 'checked')" in sql
    assert "u.id = :student" in sql
    assert params == {"student": 2}
