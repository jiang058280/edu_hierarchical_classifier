"""统计与健康检查路由。

鉴权分级（改进计划 WP-D）：/stats 需登录；/health 公开（容器 healthcheck 用）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import text

from edu_core.application.factory import get_classification_service
from edu_core.config.settings import get_settings
from edu_core.dedup.milvus_client import QuestionDedupIndex
from edu_core.security.auth import get_current_user, require_admin
from edu_core.storage.stores import StoreBundle

router = APIRouter()


@router.get("/stats", dependencies=[Depends(get_current_user)])
def stats() -> dict:
    """聚合统计（今日处理量、反馈计数、题库规模、学科分布）。"""
    service = get_classification_service()
    return service.stats()


@router.get("/health")
def health() -> dict:
    """健康检查：服务/模型版本/查重索引状态（容器 healthcheck 用）。"""
    settings = get_settings()
    service = get_classification_service()
    dedup: QuestionDedupIndex = service.dedup
    return {
        "status": "ok",
        "service": settings.app_name,
        "model_version": service.predictor.model_version,
        "quantized": service.predictor.quantized,
        "device": str(service.predictor.device),
        "dedup_available": dedup.available(),
        "dedup_unavailable_reason": dedup.unavailable_reason(),
    }


@router.get("/admin/overview", dependencies=[Depends(require_admin)])
def admin_overview(_: dict = Depends(require_admin)) -> dict:
    """E4 管理员总览：汇总只读指标，备份状态来自脚本的受控状态文件。"""
    settings, stores = get_settings(), StoreBundle()
    with stores.engine.connect() as conn:
        def count(table: str) -> int:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())
        questions, classes = count("questions"), count("classes")
        rag_messages = count("rag_messages")
        refused = int(conn.execute(text(
            "SELECT COUNT(*) FROM rag_messages WHERE role='assistant' AND refused=1")).scalar_one())
        negative = int(conn.execute(text(
            "SELECT COUNT(*) FROM rag_feedback WHERE rating=-1")).scalar_one())
    backup_path = settings.abs_path(settings.reports_dir) / "verification" / "backup_status_latest.json"
    try:
        backup = json.loads(backup_path.read_text(encoding="utf-8")) if backup_path.is_file() else {"status": "unknown"}
    except (OSError, json.JSONDecodeError):
        backup = {"status": "invalid"}
    active_kb = stores.rag.get_active_version()
    return {"questions": questions, "active_classes": classes, "rag_messages": rag_messages,
            "rag_refusal_rate": round(refused / rag_messages, 4) if rag_messages else None,
            "rag_negative_rate": round(negative / rag_messages, 4) if rag_messages else None,
            "llm_calls": rag_messages, "active_kb": active_kb, "backup": backup,
            "audits": stores.audit.recent(limit=10)}
