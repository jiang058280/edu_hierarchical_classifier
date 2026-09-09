"""应用层工厂：进程级单例（对齐 knowforge 的 application.factory）。

装配顺序：settings -> stores -> active 版本 predictor -> dedup -> ClassificationService。
API 层只通过 get_classification_service() 获取服务，不自行装配依赖。
"""

from __future__ import annotations

from functools import lru_cache

from edu_core.application.assignment_service import AssignmentService
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


def reload_classification_service() -> ClassificationService:
    """热重载（改进计划 WP-H1）：清空单例缓存，按当前 active 指针重新装配并预热。

    用于版本激活/回滚后免重启生效。装配过程中完成一次真实推理预热，
    因此只有新版本可正常加载、推理可用时本函数才返回；
    抛异常即代表新版本不可用（调用方应回退指针后再次调用本函数恢复旧版本）。
    注意：Milvus 连接随旧实例释放，重载后首次查重会重建连接（懒连接，无碍）。
    """
    logger.info("热重载分类服务（active 指针变更）")
    get_classification_service.cache_clear()
    return get_classification_service()


@lru_cache(maxsize=1)
def get_assignment_service() -> AssignmentService:
    """返回作业链路的进程级服务实例。"""
    settings = get_settings()
    return AssignmentService(stores=StoreBundle(settings=settings))
