"""学生作业路由协议测试，不连接 MySQL。"""

from edu_core.api import student
from edu_core.application import factory


class FakeService:
    def __init__(self):
        self.calls = []

    def list_for_student(self, student_id):
        self.calls.append(("list", student_id))
        return [{"id": 8, "status": "not_started"}]

    def get_for_student(self, assignment_id, student_id):
        self.calls.append(("get", assignment_id, student_id))
        return {"id": assignment_id, "questions": []}

    def submit(self, assignment_id, student_id, answers):
        self.calls.append(("submit", assignment_id, student_id, answers))
        return {"submission_id": 9, "status": "submitted", "auto_score": 100.0}

    def get_result(self, assignment_id, student_id):
        self.calls.append(("result", assignment_id, student_id))
        return {"assignment": {"id": assignment_id}, "results": []}


def test_student_assignment_routes(monkeypatch):
    service = FakeService()
    monkeypatch.setattr(factory, "get_assignment_service", lambda: service)
    user = {"id": 6, "username": "student6", "role": "student"}

    assert student.list_assignments(user) == {
        "items": [{"id": 8, "status": "not_started"}], "total": 1}
    assert student.get_assignment(8, user)["id"] == 8
    answer = [{"question_id": 10, "answer": "A"}]
    assert student.submit_assignment(8, {"answers": answer}, user)["auto_score"] == 100.0
    assert student.assignment_result(8, user)["assignment"]["id"] == 8
    assert service.calls == [
        ("list", 6), ("get", 8, 6), ("submit", 8, 6, answer), ("result", 8, 6)]


def test_student_taxonomy_exposes_answer_modes():
    result = student.student_question_taxonomy({"id": 6})
    by_name = {item["name"]: item for item in result["types"]}
    assert by_name["多选题"]["answer_mode"] == "choice_multiple"
    assert by_name["判断题"]["answer_mode"] == "judgment"
