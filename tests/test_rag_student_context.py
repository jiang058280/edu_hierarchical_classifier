from __future__ import annotations

from edu_core.api import student


def test_contextual_query_is_built_server_side(monkeypatch):
    context = {
        "assignment_id": 3, "question_id": 8, "subject": "数学", "content": "真实题干",
        "student_answer": "学生作答", "answer": "参考答案", "analysis": "教师解析",
    }
    monkeypatch.setattr(student, "_assignment_question_context", lambda *args: context)

    query, returned = student._chat_query_from_payload({
        "assignment_id": "3", "question_id": "8", "action": "why_wrong",
        "query": "客户端试图覆盖上下文", "content": "伪造题干",
    }, 9)

    assert returned is context
    assert "真实题干" in query and "学生作答" in query and "教师解析" in query
    assert "伪造题干" not in query and "客户端试图覆盖上下文" not in query


def test_plain_query_does_not_create_question_context():
    query, context = student._chat_query_from_payload({"query": "什么是函数？"}, 9)
    assert query == "什么是函数？"
    assert context is None
