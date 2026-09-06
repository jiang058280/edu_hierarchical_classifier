"""页面路由：托管原生静态前端。

平台 M0 起按门户组织：
  /          门户选择页（教师入口 / 学生入口）
  /classify  题目分类页（教师 AI 录入的在线工具）
  /teacher   教师门户（登录 + 班级管理）
  /student   学生门户（登录 + 加入班级）
  /admin     治理工作台
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from edu_core.config.settings import get_settings

router = APIRouter()


def _page(filename: str) -> FileResponse:
    settings = get_settings()
    return FileResponse(settings.abs_path(settings.static_dir) / filename)


@router.get("/")
def portal() -> FileResponse:
    """门户选择页：教师入口 / 学生入口。"""
    return _page("portal.html")


@router.get("/classify")
def classify_page() -> FileResponse:
    """题目分类页。"""
    return _page("index.html")


@router.get("/teacher")
def teacher_portal() -> FileResponse:
    """教师门户。"""
    return _page("teacher.html")


@router.get("/student")
def student_portal() -> FileResponse:
    """学生门户。"""
    return _page("student.html")


@router.get("/admin")
def admin() -> FileResponse:
    """治理工作台。"""
    return _page("admin.html")
