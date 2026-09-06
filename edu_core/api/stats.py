"""统计与健康检查路由。

鉴权分级（改进计划 WP-D）：/stats 需登录；/health 公开（容器 healthcheck 用）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from edu_core.application.factory import get_classification_service
from edu_core.config.settings import get_settings
from edu_core.dedup.milvus_client import QuestionDedupIndex
from edu_core.security.auth import get_current_user

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
