"""题库路由：增删查 / 组卷 / 学情分析（路径与字段兼容旧前端）。

鉴权分级（改进计划 WP-D）：查询/入库/组卷/分析需登录；删除题目需 admin 并写审计。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from edu_core.api.dependencies import client_key, rate_limit
from edu_core.api.schemas import GeneratePaperRequest, SaveQuestionRequest
from edu_core.application.factory import get_classification_service
from edu_core.security.auth import get_current_user, require_admin
from edu_core.storage.stores import StoreBundle

router = APIRouter()


@router.post("/questions", dependencies=[Depends(rate_limit), Depends(get_current_user)])
def save_question(req: SaveQuestionRequest) -> dict:
    """题目入库（带 Milvus 语义查重提示）。"""
    service = get_classification_service()
    return service.save_question(
        text=req.text, subject=req.subject, question_type=req.q_type,
        knowledge_point=req.knowledge, source=req.source)


@router.get("/questions", dependencies=[Depends(rate_limit), Depends(get_current_user)])
def list_questions(subject: str = "", q_type: str = "", keyword: str = "",
                   limit: int = 100, offset: int = 0) -> dict:
    """题目列表（筛选 + 分页）。"""
    service = get_classification_service()
    return service.list_questions(
        subject=subject, question_type=q_type, keyword=keyword,
        limit=max(1, min(limit, 500)), offset=max(0, offset))


@router.delete("/questions/{question_id}",
               dependencies=[Depends(rate_limit), Depends(require_admin)])
def delete_question(question_id: int, request: Request,
                    user: dict[str, Any] = Depends(require_admin)) -> dict:
    """删除题目（admin；同时删除 Milvus 向量，动作写审计）。"""
    service = get_classification_service()
    result = service.delete_question(question_id)
    StoreBundle().audit.insert(
        action="delete_question", user_id=user.get("id") or None,
        username=user.get("username"), resource=f"questions/{question_id}",
        detail={"question_id": question_id}, client_ip=client_key(request))
    return result


@router.post("/papers/generate", dependencies=[Depends(rate_limit), Depends(get_current_user)])
def generate_paper(req: GeneratePaperRequest) -> dict:
    """规则式组卷 MVP（按学科/题型配比从题库取题）。"""
    service = get_classification_service()
    return service.generate_paper(subjects=req.subjects, types=req.types, count=req.count)


@router.get("/analysis", dependencies=[Depends(get_current_user)])
def analysis() -> dict:
    """学情分析（各学科反馈正确率 + 薄弱知识点）。"""
    service = get_classification_service()
    return service.analysis()
