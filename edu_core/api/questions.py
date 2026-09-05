"""题库路由：增删查 / 组卷 / 学情分析（路径与字段兼容旧前端）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from edu_core.api.dependencies import rate_limit
from edu_core.api.schemas import GeneratePaperRequest, SaveQuestionRequest
from edu_core.application.factory import get_classification_service

router = APIRouter()


@router.post("/questions", dependencies=[Depends(rate_limit)])
def save_question(req: SaveQuestionRequest) -> dict:
    """题目入库（带 Milvus 语义查重提示）。"""
    service = get_classification_service()
    return service.save_question(
        text=req.text, subject=req.subject, question_type=req.q_type,
        knowledge_point=req.knowledge, source=req.source)


@router.get("/questions", dependencies=[Depends(rate_limit)])
def list_questions(subject: str = "", q_type: str = "", keyword: str = "",
                   limit: int = 100, offset: int = 0) -> dict:
    """题目列表（筛选 + 分页）。"""
    service = get_classification_service()
    return service.list_questions(
        subject=subject, question_type=q_type, keyword=keyword,
        limit=max(1, min(limit, 500)), offset=max(0, offset))


@router.delete("/questions/{question_id}", dependencies=[Depends(rate_limit)])
def delete_question(question_id: int) -> dict:
    """删除题目（同时删除 Milvus 向量）。"""
    service = get_classification_service()
    return service.delete_question(question_id)


@router.post("/papers/generate", dependencies=[Depends(rate_limit)])
def generate_paper(req: GeneratePaperRequest) -> dict:
    """规则式组卷 MVP（按学科/题型配比从题库取题）。"""
    service = get_classification_service()
    return service.generate_paper(subjects=req.subjects, types=req.types, count=req.count)


@router.get("/analysis")
def analysis() -> dict:
    """学情分析（各学科反馈正确率 + 薄弱知识点）。"""
    service = get_classification_service()
    return service.analysis()
