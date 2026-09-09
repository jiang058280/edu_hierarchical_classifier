"""教师作业路由的协议编排测试，不连接数据库。"""

from types import SimpleNamespace

from starlette.requests import Request

from edu_core.api import teacher
from edu_core.application import factory


class FakeAssignmentService:
    def __init__(self):
        self.calls = []

    def list_for_teacher(self, teacher_id, **kwargs):
        self.calls.append(("list", teacher_id, kwargs))
        return {"items": [], "total": 0, **kwargs}

    def create_assignment(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"id": 7, "paper_id": kwargs["paper_id"], "class_id": kwargs["class_id"],
                "mode": kwargs["mode"]}

    def get_for_teacher(self, assignment_id, teacher_id):
        self.calls.append(("get", assignment_id, teacher_id))
        return {"id": assignment_id}

    def list_submissions(self, assignment_id, teacher_id):
        self.calls.append(("submissions", assignment_id, teacher_id))
        return {"students": [], "total": 0, "completed": 0}

    def check_submission(self, submission_id, teacher_id, final_score):
        self.calls.append(("check", submission_id, teacher_id, final_score))
        return {"submission_id": submission_id, "status": "checked",
                "final_score": float(final_score)}


class FakeAudit:
    def __init__(self):
        self.rows = []

    def insert(self, **kwargs):
        self.rows.append(kwargs)


def _request():
    return Request({"type": "http", "method": "POST", "path": "/",
                    "headers": [], "client": ("127.0.0.1", 1234)})


def test_teacher_assignment_routes_and_audit(monkeypatch):
    service, audit = FakeAssignmentService(), FakeAudit()
    monkeypatch.setattr(factory, "get_assignment_service", lambda: service)
    monkeypatch.setattr(teacher, "_stores", lambda: SimpleNamespace(audit=audit))
    user = {"id": 3, "username": "teacher3"}

    assert teacher.list_assignments(20, 5, user)["total"] == 0
    created = teacher.create_assignment(
        {"paper_id": 1, "class_id": 2, "title": "练习", "mode": "exam",
         "allow_self_check": False}, _request(), user)
    assert created["id"] == 7
    assert teacher.get_assignment(7, user)["id"] == 7
    assert teacher.assignment_submissions(7, user)["total"] == 0
    checked = teacher.check_submission(9, {"final_score": 88.5}, _request(), user)
    assert checked["final_score"] == 88.5

    assert [row["action"] for row in audit.rows] == ["create_assignment", "check_submission"]
    assert service.calls[0] == ("list", 3, {"limit": 20, "offset": 5})
    assert service.calls[-1] == ("check", 9, 3, 88.5)
