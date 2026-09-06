"""模型版本管理路由（治理工作台后端）。

对应 knowforge 的 kb_versions 路由角色：查看版本列表、激活/回滚，
治理动作只允许显式调用，不做任何自动切换。

鉴权分级（改进计划 WP-D）：全部端点需 admin；激活/回滚写审计日志。
激活/回滚成功后自动热重载进程内预测器（改进计划 WP-H1），失败保持旧版本。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from edu_core.api.dependencies import client_key, rate_limit
from edu_core.api.schemas import ActivateModelRequest
from edu_core.application.factory import (
    get_classification_service,
    reload_classification_service,
)
from edu_core.governance.model_versions import ModelVersionManager
from edu_core.security.auth import require_admin
from edu_core.storage.stores import StoreBundle

router = APIRouter()


def _manager() -> ModelVersionManager:
    """版本管理器（独立于在线服务单例，激活后由热重载生效）。"""
    return ModelVersionManager(stores=StoreBundle())


def _audit(request: Request, user: dict[str, Any], action: str, resource: str,
           detail: dict | None = None) -> None:
    """治理动作审计留痕。"""
    StoreBundle().audit.insert(
        action=action, user_id=user.get("id") or None, username=user.get("username"),
        resource=resource, detail=detail, client_ip=client_key(request))


@router.get("/models", dependencies=[Depends(require_admin)])
def list_models(_: dict[str, Any] = Depends(require_admin)) -> dict:
    """模型版本列表（含评估指标与状态；admin）。"""
    versions = _manager().list_versions()
    active = next((v for v in versions if v["status"] == "ACTIVE"), None)
    return {"active_version": active["version"] if active else None, "versions": versions}


@router.post("/models/{version}/activate",
             dependencies=[Depends(rate_limit), Depends(require_admin)])
def activate_model(version: str, req: ActivateModelRequest, request: Request,
                   user: dict[str, Any] = Depends(require_admin)) -> dict:
    """激活指定模型版本（admin；激活前默认强校验版本目录文件齐全）。

    激活成功后热重载进程内预测器；热重载失败时把 active 指针拨回
    原服务版本并重新装配，保证 serving 版本与指针始终一致。
    """
    previous_serving = get_classification_service().predictor.model_version
    result = _manager().activate_version(version, validate_files=req.validate_files)
    try:
        reloaded = reload_classification_service()
        result["serving_version"] = reloaded.predictor.model_version
    except Exception as exc:
        _manager().activate_version(previous_serving, validate_files=False)
        reload_classification_service()
        raise RuntimeError(
            f"版本 {version} 文件校验通过但加载失败，已回滚至 {previous_serving}：{exc}") from exc
    _audit(request, user, "activate_model", f"models/{version}",
           {"version": version, "validate_files": req.validate_files,
            "serving_version": result["serving_version"], "previous": previous_serving})
    return result


@router.post("/models/rollback",
             dependencies=[Depends(rate_limit), Depends(require_admin)])
def rollback_model(request: Request,
                   user: dict[str, Any] = Depends(require_admin)) -> dict:
    """回滚到最近一次归档版本（admin；成功后热重载生效）。"""
    result = _manager().rollback_to_previous()
    try:
        reloaded = reload_classification_service()
        result["serving_version"] = reloaded.predictor.model_version
    except Exception as exc:
        result["reload_error"] = f"{type(exc).__name__}: {exc}"
    _audit(request, user, "rollback_model", "models",
           {"rolled_back_to": result.get("rolled_back_to"),
            "serving_version": result.get("serving_version")})
    return result


@router.get("/models/active", dependencies=[Depends(require_admin)])
def active_model(_: dict[str, Any] = Depends(require_admin)) -> dict:
    """当前 active 版本详情（admin）。"""
    service = get_classification_service()
    return {"serving_version": service.predictor.model_version,
            "active": _manager().get_active()}
