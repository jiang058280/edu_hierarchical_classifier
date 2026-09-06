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
    # no-cache：页面更新后浏览器必须拉新版本，避免旧脚本缓存引发的诡异故障
    return FileResponse(settings.abs_path(settings.static_dir) / filename,
                        headers={"Cache-Control": "no-cache"})


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


@router.get("/teacher/bank")
def teacher_bank() -> FileResponse:
    """题库管理。"""
    return _page("teacher_bank.html")


@router.get("/teacher/entry")
def teacher_entry() -> FileResponse:
    """AI 智能录入。"""
    return _page("teacher_entry.html")


@router.get("/teacher/papers")
def teacher_papers() -> FileResponse:
    """组卷与试卷。"""
    return _page("teacher_papers.html")


@router.get("/login")
def login_page() -> FileResponse:
    """独立登录页（portal=teacher/student）。"""
    return _page("login.html")


@router.get("/student")
def student_portal() -> FileResponse:
    """学生门户。"""
    return _page("student.html")


@router.get("/admin")
def admin() -> FileResponse:
    """治理工作台。"""
    return _page("admin.html")
