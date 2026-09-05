"""Edu Hierarchical Classifier 的 FastAPI 应用入口（薄入口，对齐 knowforge-rag-platform）。

本文件只负责四件事：
1. 创建 FastAPI 应用并配置 CORS / 静态资源；
2. 启动时执行必需校验：文件系统 preflight -> MySQL schema bootstrap
   -> active 模型版本校验 -> 预测器预热（首次真实推理）；
3. 注册 edu_core.api 下拆分后的路由（/api/v1 前缀 + /api 旧契约别名）；
4. 启动后异步探测 Milvus 查重索引可用性（增强组件，不阻塞启动）。

为什么保持入口很薄：
- 入口越薄，越容易确认主链路没有隐藏旁路和技术降级路径；
- 接口按 分类/题库/统计/版本治理 拆分后边界清晰；
- 意图识别、检索、推理细节一律不放在这里（属于 edu_core 各层）。

启动：venv\\Scripts\\python -m uvicorn app:app --host 127.0.0.1 --port 7860
      或 venv\\Scripts\\python app.py
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from edu_core.api import classify, models, pages, questions, stats
from edu_core.api.error_handlers import register_api_exception_handlers
from edu_core.application.factory import get_classification_service
from edu_core.config.logging_config import get_logger
from edu_core.config.preflight import validate_runtime_environment
from edu_core.config.settings import get_settings
from edu_core.governance.model_versions import ModelVersionManager
from edu_core.storage.bootstrap import bootstrap_mysql_schema
from edu_core.storage.stores import StoreBundle

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """FastAPI 生命周期：预热完成后才接收流量（对齐 knowforge 启动顺序）。"""
    await warmup_runtime()
    yield


app = FastAPI(
    title=settings.app_name,
    description="BERT 多任务层级分类（学科/题型/知识点）· 企业版：MySQL 持久化 + 模型版本治理 + 评测门禁",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
register_api_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(settings.abs_path(settings.static_dir))), name="static")


async def warmup_runtime() -> None:
    """启动预热：preflight -> schema -> active 版本 -> 模型加载与首推理。"""
    validate_runtime_environment(settings)
    logger.info("Runtime preflight passed（模型资产/标签文件校验通过）")

    schema_summary = await asyncio.to_thread(bootstrap_mysql_schema, settings)
    logger.info("Runtime MySQL schema bootstrap passed: %s", schema_summary)

    manager = ModelVersionManager(stores=StoreBundle(settings=settings), settings=settings)
    active = manager.get_active()
    logger.info("Runtime active model version check passed: %s", active["version"])

    # 进程级单例装配：加载 active 版本权重 + 完成一次真实推理预热
    service = await asyncio.to_thread(get_classification_service)
    warm = await asyncio.to_thread(service.predictor.predict, "预热：已知函数 f(x)=x²+1，求 f(2) 的值。")
    logger.info("Runtime predictor warmup passed: version=%s latency=%.2fms",
                warm["model_version"], warm["latency_ms"])

    # Milvus 查重索引探测（增强组件，失败仅告警不阻塞）
    probe = await asyncio.to_thread(service.dedup.available)
    if probe:
        logger.info("Runtime dedup index available")
    else:
        logger.warning("Runtime dedup index unavailable（查重降级跳过）: %s",
                       service.dedup.unavailable_reason())


# 路由注册：/api/v1 正式前缀 + /api 旧契约别名（同一 router 复用，旧前端无需改路径）
app.include_router(pages.router)  # 根路径 / 与 /admin 不带前缀
for prefix in ("/api/v1", "/api"):
    app.include_router(pages.router, prefix=prefix)
    app.include_router(classify.router, prefix=prefix)
    app.include_router(questions.router, prefix=prefix)
    app.include_router(stats.router, prefix=prefix)
    app.include_router(models.router, prefix=prefix)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.api_host, port=settings.api_port, reload=False)
