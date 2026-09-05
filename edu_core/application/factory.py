"""应用层工厂：进程级单例（对齐 knowforge 的 application.factory）。

装配顺序：settings -> stores -> active 版本 predictor -> dedup -> ClassificationService。
API 层只通过 get_classification_service() 获取服务，不自行装配依赖。
"""

from __future__ import annotations

from functools import lru_cache

from edu_core.application.service import ClassificationService
from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings
from edu_core.dedup.milvus_client import QuestionDedupIndex
from edu_core.governance.model_versions import ModelVersionManager
from edu_core.inference.predictor import HierarchicalPredictor
from edu_core.storage.stores import StoreBundle

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_classification_service() -> ClassificationService:
    """进程级单例：加载 active 模型版本并装配全部依赖。"""
    settings = get_settings()
    stores = StoreBundle(settings=settings)
    manager = ModelVersionManager(stores=stores, settings=settings)
    active = manager.get_active()
    active_dir = manager.get_active_dir()
    logger.info("装配分类服务：active 模型版本 %s（%s）", active["version"], active_dir)

    predictor = HierarchicalPredictor(active_dir, settings=settings, verbose=True)
    dedup = QuestionDedupIndex(settings, embed_fn=predictor.embed)
    return ClassificationService(predictor=predictor, stores=stores, dedup=dedup, settings=settings)
