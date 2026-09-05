"""模型版本管理路由（治理工作台后端）。

对应 knowforge 的 kb_versions 路由角色：查看版本列表、激活/回滚，
治理动作只允许显式调用，不做任何自动切换。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from edu_core.api.dependencies import rate_limit
from edu_core.api.schemas import ActivateModelRequest
from edu_core.application.factory import get_classification_service
from edu_core.governance.model_versions import ModelVersionManager
from edu_core.storage.stores import StoreBundle

router = APIRouter()


def _manager() -> ModelVersionManager:
    """版本管理器（独立于在线服务单例，激活后由服务重启/重载生效）。"""
    return ModelVersionManager(stores=StoreBundle())


@router.get("/models")
def list_models() -> dict:
    """模型版本列表（含评估指标与状态）。"""
    versions = _manager().list_versions()
    active = next((v for v in versions if v["status"] == "ACTIVE"), None)
    return {"active_version": active["version"] if active else None, "versions": versions}


@router.post("/models/{version}/activate", dependencies=[Depends(rate_limit)])
def activate_model(version: str, req: ActivateModelRequest) -> dict:
    """激活指定模型版本（激活前默认强校验版本目录文件齐全）。"""
    return _manager().activate_version(version, validate_files=req.validate_files)


@router.post("/models/rollback", dependencies=[Depends(rate_limit)])
def rollback_model() -> dict:
    """回滚到最近一次归档版本。"""
    return _manager().rollback_to_previous()


@router.get("/models/active")
def active_model() -> dict:
    """当前 active 版本详情。"""
    service = get_classification_service()
    return {"serving_version": service.predictor.model_version,
            "active": _manager().get_active()}
