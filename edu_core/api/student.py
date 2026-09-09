"""学生端路由组（平台计划 M0/M2，require_student 门禁）。

M0：加入班级、我的班级；M2：作业列表、作答提交与结果查看。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from edu_core.security.auth import require_student
from edu_core.storage.stores import StoreBundle

router = APIRouter()


@router.post("/student/join-class")
def join_class(payload: dict,
               user: dict[str, Any] = Depends(require_student)) -> dict:
    """学生凭邀请码加入班级：{invite_code}。重复加入以最后一次为准。"""
    code = (payload.get("invite_code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="邀请码不能为空")
    stores = StoreBundle()
    cls = stores.classes.get_by_invite_code(code)
    if not cls:
        raise HTTPException(status_code=404, detail="邀请码无效或班级已停用")
    stores.classes.join(cls["id"], int(user["id"]))
    return {"status": "ok", "class": cls}


@router.get("/student/my-class")
def my_class(user: dict[str, Any] = Depends(require_student)) -> dict:
    """我的班级信息与同学名单（仅返回姓名/学号，保护隐私）。"""
    stores = StoreBundle()
    me = stores.users.get_by_username(user["username"])
    if not me or not me.get("class_id"):
        return {"class": None, "classmates": []}
    cls = stores.classes.get(int(me["class_id"]))
    classmates = stores.users.list_by_class(int(me["class_id"]))
    return {
        "class": cls,
        "classmates": [{"real_name": c.get("real_name"), "student_no": c.get("student_no")}
                       for c in classmates if c["id"] != user["id"]],
    }


@router.get("/student/question-taxonomy")
def student_question_taxonomy(
        _: dict[str, Any] = Depends(require_student)) -> dict:
    """学生作答控件所需的统一题型及 answer_mode。"""
    from edu_core.application.question_taxonomy import taxonomy_payload

    return taxonomy_payload()


@router.get("/student/assignments")
def list_assignments(user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    items = get_assignment_service().list_for_student(int(user["id"]))
    return {"items": items, "total": len(items)}


@router.get("/student/assignments/{assignment_id}")
def get_assignment(assignment_id: int,
                   user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().get_for_student(assignment_id, int(user["id"]))


@router.post("/student/assignments/{assignment_id}/submit")
def submit_assignment(assignment_id: int, payload: dict,
                      user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().submit(
        assignment_id, int(user["id"]), payload.get("answers"))


@router.get("/student/assignments/{assignment_id}/result")
def assignment_result(assignment_id: int,
                      user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().get_result(assignment_id, int(user["id"]))
