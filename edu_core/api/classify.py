"""分类与反馈路由。

兼容说明：同一 router 在 app.py 中以 /api/v1 与 /api 两个前缀各注册一次，
旧前端 /api/classify、/api/feedback、/api/history 无需改动即可继续使用。

鉴权分级（改进计划 WP-D）：classify / feedback / history 需登录（teacher 及以上）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from edu_core.api.dependencies import rate_limit
from edu_core.api.schemas import ClassifyRequest, FeedbackRequest
from edu_core.application.factory import get_classification_service
from edu_core.security.auth import get_current_user

router = APIRouter()


@router.post("/classify", dependencies=[Depends(rate_limit), Depends(get_current_user)])
def classify(req: ClassifyRequest) -> dict:
    """单条题目三级分类（在线主链路）。"""
    service = get_classification_service()
    return service.classify(req.text)


@router.post("/feedback", dependencies=[Depends(rate_limit), Depends(get_current_user)])
def feedback(req: FeedbackRequest) -> dict:
    """提交反馈（关联分类留痕，供 Bad Case 闭环）。"""
    service = get_classification_service()
    return service.submit_feedback(
        is_correct=req.correct,
        classification_id=req.classification_id,
        question_text=req.text,
        subject=req.subject,
        corrected_subject=req.corrected_subject,
        corrected_type=req.corrected_type,
        corrected_knowledge=req.corrected_knowledge,
        comment=req.comment,
    )


@router.get("/history", dependencies=[Depends(get_current_user)])
def history(limit: int = 20) -> list[dict]:
    """最近分类历史。"""
    limit = max(1, min(limit, 100))
    service = get_classification_service()
    return service.history(limit=limit)
