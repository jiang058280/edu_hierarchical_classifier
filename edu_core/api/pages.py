"""页面路由：托管原生静态前端（分类页 + 治理工作台）。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from edu_core.config.settings import get_settings

router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    """分类问答页（static/index.html）。"""
    settings = get_settings()
    return FileResponse(settings.abs_path(settings.static_dir) / "index.html")


@router.get("/admin")
def admin() -> FileResponse:
    """治理工作台（static/admin.html）：版本/评估报告/反馈/统计。"""
    settings = get_settings()
    return FileResponse(settings.abs_path(settings.static_dir) / "admin.html")
