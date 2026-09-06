"""教师端路由组（平台计划 M0，require_teacher 门禁）。

现有 classify/feedback/questions 等教研能力后续按 M1 计划逐步归入本组；
本文件先落 M0 范围：班级管理（建班/名单/邀请码）。
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from edu_core.security.auth import require_teacher
from edu_core.storage.stores import StoreBundle

router = APIRouter()


@router.get("/teacher/classes")
def my_classes(user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """我创建的班级列表（含学生数）。"""
    classes = StoreBundle().classes.list_by_teacher(int(user["id"]))
    return {"classes": classes}


@router.post("/teacher/classes")
def create_class(payload: dict, user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """创建班级：{name, grade_band, grade} → 生成 6 位邀请码。"""
    name = (payload.get("name") or "").strip()
    grade_band = (payload.get("grade_band") or "").strip()
    grade = (payload.get("grade") or "").strip()
    if not name or grade_band not in ("初中", "高中") or not grade:
        raise HTTPException(
            status_code=400, detail="name / grade(年级) 必填，grade_band 仅支持 初中/高中")
    invite_code = secrets.token_hex(3).upper()  # 6 位十六进制邀请码
    stores = StoreBundle()
    try:
        class_id = stores.classes.create(
            name, grade_band, grade, created_by=int(user["id"]), invite_code=invite_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    StoreBundle().audit.insert(
        action="create_class", user_id=user.get("id") or None,
        username=user.get("username"), resource=f"classes/{class_id}",
        detail={"name": name, "grade_band": grade_band, "grade": grade})
    return {"status": "ok", "id": class_id, "invite_code": invite_code}


@router.get("/teacher/classes/{class_id}/students")
def class_students(class_id: int, user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """班级学生名册（仅班级创建教师可看）。"""
    stores = StoreBundle()
    cls = stores.classes.get(class_id)
    if not cls or cls["created_by"] != int(user["id"]):
        raise HTTPException(status_code=404, detail="班级不存在")
    return {"class": cls, "students": stores.users.list_by_class(class_id)}
