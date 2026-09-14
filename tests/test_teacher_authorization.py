from edu_core.application.assignment_service import AssignmentPermissionError, AssignmentService


class _Papers:
    def get(self, _): return {"created_by": 3, "questions": [{"id": 1}]}


class _Classes:
    def get(self, _): return {"created_by": 3, "is_active": True}


class _Assignments:
    def get_detail(self, _): return {"created_by": 3}


class _Stores:
    papers = _Papers()
    classes = _Classes()
    assignments = _Assignments()


def test_teacher_cannot_publish_another_teachers_paper_or_class():
    service = AssignmentService(_Stores())
    try:
        service.create_assignment(paper_id=1, class_id=2, title="复习", created_by=4)
    except AssignmentPermissionError as exc:
        assert "自己创建的试卷" in str(exc)
    else:
        raise AssertionError("应拒绝跨教师发布")
