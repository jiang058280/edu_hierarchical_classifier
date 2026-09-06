"""统一异常处理：全部错误转 JSON {"error": ...}，禁止堆栈泄漏到响应。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from edu_core.api.dependencies import RateLimitExceeded
from edu_core.application.service import ValidationError
from edu_core.config.logging_config import get_logger
from edu_core.config.preflight import PreflightError
from edu_core.inference.predictor import ModelArtifactError
from edu_core.storage.stores import sqlalchemy_error_to_message

logger = get_logger(__name__)


def register_api_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(HTTPException)
    async def _http_exception(_: Request, exc: HTTPException):
        # 统一 {"error": ...} 形态（含 401/403 鉴权错误），保留 WWW-Authenticate 头
        return JSONResponse(
            {"error": str(exc.detail)}, status_code=exc.status_code,
            headers=exc.headers or {})

    @app.exception_handler(ValidationError)
    async def _validation_error(_: Request, exc: ValidationError):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit(_: Request, exc: RateLimitExceeded):
        return JSONResponse(
            {"error": f"请求过于频繁，请 {exc.retry_after} 秒后重试"},
            status_code=429, headers={"Retry-After": str(exc.retry_after)})

    @app.exception_handler(PreflightError)
    async def _preflight(_: Request, exc: PreflightError):
        return JSONResponse({"error": f"启动前置校验失败：{exc}"}, status_code=503)

    @app.exception_handler(ModelArtifactError)
    async def _artifact(_: Request, exc: ModelArtifactError):
        return JSONResponse({"error": f"模型产物异常：{exc}"}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse(
            {"error": f"参数校验失败：{field} {first.get('msg', '')}".strip()},
            status_code=400)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        # 治理/业务层的值错误（如"没有可回滚的归档版本"、"版本已存在"）→ 400
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        db_msg = sqlalchemy_error_to_message(exc)
        if db_msg:
            logger.error("请求 %s 数据库异常：%s", request.url.path, db_msg)
            return JSONResponse({"error": f"数据库异常：{db_msg}"}, status_code=503)
        logger.exception("请求 %s 未处理异常", request.url.path)
        return JSONResponse({"error": "服务内部错误，请查看服务端日志"}, status_code=500)
