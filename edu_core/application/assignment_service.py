"""作业业务编排：归属校验、截止控制、判分、提交与教师批改。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

from edu_core.application.grading import grade_submission, is_objective_type


class AssignmentError(ValueError):
    """作业业务异常基类。"""


class AssignmentValidationError(AssignmentError):
    """输入或业务规则不合法。"""


class AssignmentNotFoundError(AssignmentError):
    """目标资源不存在。"""


class AssignmentConflictError(AssignmentError):
    """当前状态与操作冲突，例如重复提交。"""


class AssignmentPermissionError(AssignmentError):
    """用户不拥有目标班级、试卷、作业或提交。"""


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError as exc:
        raise AssignmentValidationError(f"无效的截止时间：{value}") from exc


class AssignmentService:
    """M2 作业核心服务；路由层只负责协议转换和鉴权。"""

    def __init__(self, stores, now_fn: Callable[[], datetime] | None = None):
        self.stores = stores
        self.now_fn = now_fn or datetime.now

    def create_assignment(self, *, paper_id: int, class_id: int, title: str,
                          created_by: int, mode: str = "homework",
                          due_at: datetime | str | None = None,
                          allow_self_check: bool = True) -> dict:
        try:
            paper_id = int(paper_id)
            class_id = int(class_id)
        except (TypeError, ValueError) as exc:
            raise AssignmentValidationError("paper_id 与 class_id 必须是整数") from exc
        title = str(title or "").strip()
        if not title:
            raise AssignmentValidationError("作业标题不能为空")
        if len(title) > 128:
            raise AssignmentValidationError("作业标题不能超过 128 个字符")
        if mode not in ("homework", "exam"):
            raise AssignmentValidationError("作业模式仅支持 homework/exam")
        if not isinstance(allow_self_check, bool):
            raise AssignmentValidationError("allow_self_check 必须是布尔值")

        paper = self.stores.papers.get(paper_id)
        if not paper:
            raise AssignmentNotFoundError(f"试卷不存在：{paper_id}")
        if int(paper["created_by"]) != int(created_by):
            raise AssignmentPermissionError("只能发布自己创建的试卷")
        if not paper.get("questions"):
            raise AssignmentValidationError("空试卷不能发布为作业")

        target_class = self.stores.classes.get(class_id)
        if not target_class or not target_class.get("is_active", True):
            raise AssignmentNotFoundError(f"班级不存在或已停用：{class_id}")
        if int(target_class["created_by"]) != int(created_by):
            raise AssignmentPermissionError("只能向自己创建的班级发布作业")

        parsed_due_at = _as_datetime(due_at)
        if parsed_due_at is not None and parsed_due_at <= self.now_fn():
            raise AssignmentValidationError("截止时间必须晚于当前时间")

        try:
            assignment_id = self.stores.assignments.create(
                paper_id=paper_id, class_id=class_id, title=title,
                created_by=int(created_by), mode=mode, due_at=parsed_due_at,
                allow_self_check=bool(allow_self_check),
            )
        except ValueError as exc:
            raise AssignmentConflictError(str(exc)) from exc
        return self.get_for_teacher(assignment_id, teacher_id=created_by)

    def list_for_teacher(self, teacher_id: int, *, limit: int = 50,
                         offset: int = 0) -> dict:
        if not 1 <= int(limit) <= 100:
            raise AssignmentValidationError("limit 需在 1~100 之间")
        if int(offset) < 0:
            raise AssignmentValidationError("offset 不能为负数")
        items, total = self.stores.assignments.list_by_teacher(
            int(teacher_id), limit=int(limit), offset=int(offset))
        return {"items": items, "total": total, "limit": int(limit), "offset": int(offset)}

    def list_for_student(self, student_id: int) -> list[dict]:
        self._require_student(student_id, require_class=False)
        return self.stores.assignments.list_by_student(int(student_id))

    def get_for_teacher(self, assignment_id: int, teacher_id: int) -> dict:
        detail = self.stores.assignments.get_detail(int(assignment_id))
        if not detail:
            raise AssignmentNotFoundError(f"作业不存在：{assignment_id}")
        if int(detail["created_by"]) != int(teacher_id):
            raise AssignmentPermissionError("只能查看自己发布的作业")
        return detail

    def list_submissions(self, assignment_id: int, teacher_id: int) -> dict:
        """教师查看作业覆盖的全班学生及各自提交/批改状态。"""
        assignment = self.get_for_teacher(assignment_id, teacher_id)
        students = self.stores.assignments.list_submissions(int(assignment_id))
        completed = sum(1 for item in students if item["status"] in ("submitted", "checked"))
        return {
            "assignment": {
                key: assignment.get(key)
                for key in ("id", "title", "class_id", "class_name", "mode", "due_at")
            },
            "students": students,
            "total": len(students),
            "completed": completed,
        }

    def get_for_student(self, assignment_id: int, student_id: int,
                        *, reveal_answers: bool = False) -> dict:
        student = self._require_student(student_id, require_class=False)
        detail = self.stores.assignments.get_detail(int(assignment_id), int(student_id))
        if not detail:
            raise AssignmentNotFoundError(f"作业不存在：{assignment_id}")
        historical = (detail.get("submission") or {}).get("status") in ("submitted", "checked")
        if detail["class_id"] != student.get("class_id") and not historical:
            raise AssignmentPermissionError("该作业不属于学生所在班级")

        result = deepcopy(detail)
        result["server_time"] = self.now_fn().astimezone().isoformat()
        if not reveal_answers:
            for question in result.get("questions", []):
                question.pop("answer", None)
                question.pop("analysis", None)
        return result

    def get_result(self, assignment_id: int, student_id: int) -> dict:
        """提交后返回得分、学生答案、参考答案与解析；未提交时禁止提前查看。"""
        detail = self.get_for_student(
            assignment_id, student_id, reveal_answers=True)
        submission = detail.get("submission") or {}
        if submission.get("status") not in ("submitted", "checked"):
            raise AssignmentConflictError("作业尚未提交，不能查看答案与解析")
        answer_by_id = {
            int(item["question_id"]): item for item in detail.get("answers", [])
        }
        results = []
        for question in detail.get("questions", []):
            record = answer_by_id.get(int(question["id"]), {})
            results.append({
                **question,
                "student_answer": record.get("answer"),
                "is_correct": record.get("is_correct"),
                "self_assessed": not is_objective_type(question.get("question_type", ""))
                and record.get("is_correct") is not None,
            })
        return {
            "assignment": {
                key: detail.get(key)
                for key in ("id", "title", "subject", "class_name", "mode", "due_at")
            },
            "submission": submission,
            "results": results,
        }

    def submit(self, assignment_id: int, student_id: int,
               answers: list[dict[str, Any]]) -> dict:
        student = self._require_student(student_id)
        detail = self.stores.assignments.get_detail(int(assignment_id), int(student_id))
        if not detail:
            raise AssignmentNotFoundError(f"作业不存在：{assignment_id}")
        if int(detail["class_id"]) != int(student["class_id"]):
            raise AssignmentPermissionError("该作业不属于学生所在班级")
        if (detail.get("submission") or {}).get("status") in ("submitted", "checked"):
            raise AssignmentConflictError("该作业已经提交，不能重复提交")

        due_at = _as_datetime(detail.get("due_at"))
        if due_at is not None and self.now_fn() >= due_at:
            raise AssignmentConflictError("作业已截止，不能提交")
        if not isinstance(answers, list):
            raise AssignmentValidationError("answers 必须是数组")

        questions = detail.get("questions") or []
        if not questions:
            raise AssignmentConflictError("该作业已无可用题目，请联系教师")
        question_by_id = {int(item["id"]): item for item in questions}
        answer_by_id: dict[int, dict[str, Any]] = {}
        for item in answers:
            if not isinstance(item, dict) or "question_id" not in item:
                raise AssignmentValidationError("每份答案必须包含 question_id")
            try:
                if isinstance(item["question_id"], bool) or str(item["question_id"]) != str(int(item["question_id"])):
                    raise ValueError("not an integer id")
                question_id = int(item["question_id"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise AssignmentValidationError("question_id 必须是整数") from exc
            if question_id not in question_by_id:
                raise AssignmentValidationError(f"题目不属于该作业：{question_id}")
            answer = item.get("answer")
            if answer is not None and not isinstance(answer, str):
                raise AssignmentValidationError("answer 必须是字符串或 null")
            if answer is not None and len(answer) > 512:
                raise AssignmentValidationError(f"题目 {question_id} 的答案不能超过 512 个字符")
            answer_by_id[question_id] = item

        normalized_answers = [
            {"question_id": qid, "answer": answer_by_id.get(qid, {}).get("answer")}
            for qid in question_by_id
        ]
        graded = grade_submission(questions, normalized_answers)
        graded_by_id = {int(item["question_id"]): item["is_correct"] for item in graded}

        records = []
        for question_id, question in question_by_id.items():
            submitted = answer_by_id.get(question_id, {})
            is_correct = graded_by_id[question_id]
            self_check = submitted.get("self_check")
            if not is_objective_type(question.get("question_type", "")) and self_check is not None:
                if not detail.get("allow_self_check"):
                    raise AssignmentValidationError("该作业不允许主观题自评")
                if not isinstance(self_check, bool):
                    raise AssignmentValidationError("self_check 必须是布尔值")
                is_correct = self_check
            records.append({
                "question_id": question_id,
                "answer": submitted.get("answer"),
                "is_correct": is_correct,
                "auto_gradable": is_objective_type(question.get("question_type", "")),
            })

        try:
            return self.stores.assignments.submit(
                assignment_id=int(assignment_id), student_id=int(student_id), answers=records)
        except ValueError as exc:
            raise AssignmentConflictError(str(exc)) from exc

    def check_submission(self, submission_id: int, teacher_id: int,
                         final_score: float) -> dict:
        try:
            score = float(final_score)
        except (TypeError, ValueError) as exc:
            raise AssignmentValidationError("最终分数必须是数字") from exc
        if not 0 <= score <= 100:
            raise AssignmentValidationError("最终分数需在 0~100 之间")

        submission = self.stores.assignments.get_submission(int(submission_id))
        if not submission:
            raise AssignmentNotFoundError(f"提交记录不存在：{submission_id}")
        if int(submission["created_by"]) != int(teacher_id):
            raise AssignmentPermissionError("只能批改自己发布的作业")
        if submission["status"] not in ("submitted", "checked"):
            raise AssignmentConflictError("学生尚未提交，不能批改")
        if not self.stores.assignments.check(int(submission_id), score):
            raise AssignmentConflictError("提交状态已变化，请刷新后重试")
        return {"submission_id": int(submission_id), "status": "checked", "final_score": score}

    def _require_student(self, student_id: int, *, require_class: bool = True) -> dict:
        student = self.stores.users.get(int(student_id))
        if not student or student.get("role") != "student" or not student.get("is_active", True):
            raise AssignmentNotFoundError(f"学生不存在或已停用：{student_id}")
        if require_class and student.get("class_id") is None:
            raise AssignmentValidationError("学生尚未加入班级")
        return student
