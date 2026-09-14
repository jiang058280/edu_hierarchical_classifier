"""学生端路由组（平台计划 M0/M2，require_student 门禁）。

M0：加入班级、我的班级；M2：作业列表、作答提交与结果查看。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from edu_core.security.auth import require_student
from edu_core.storage.stores import StoreBundle

router = APIRouter()


def _student_retrieval_filters(user: dict[str, Any], subject: str | None = None):
    """检索范围仅由已登录学生档案派生，前端不能越权传入学段或年级。"""
    from edu_core.rag.retrieval import RetrievalFilters

    profile = StoreBundle().users.get(int(user["id"]))
    return RetrievalFilters(
        subject=subject,
        grade_band=profile.get("grade_band") if profile else None,
        grade=profile.get("grade") if profile else None,
    )


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _assignment_question_context(assignment_id: int, question_id: int, student_id: int) -> dict:
    """仅从学生已提交作业结果读取错题上下文，禁止客户端提交题干或答案。"""
    from edu_core.application.factory import get_assignment_service

    result = get_assignment_service().get_result(assignment_id, student_id)
    item = next((row for row in result.get("results", []) if int(row["question_id"]) == question_id), None)
    if not item:
        raise ValueError("题目不属于该作业")
    assignment = result["assignment"]
    return {
        "assignment_id": assignment_id, "question_id": question_id,
        "assignment_title": assignment.get("title"), "subject": assignment.get("subject"),
        "question_type": item.get("question_type"), "content": item.get("content"),
        "student_answer": item.get("student_answer"), "answer": item.get("answer"),
        "analysis": item.get("analysis"), "is_correct": item.get("is_correct"),
    }


def _chat_query_from_payload(payload: dict, student_id: int) -> tuple[str, dict | None]:
    """错题问答的完整上下文由服务端拼装，避免前端越权篡改答案或解析。"""
    if not payload.get("question_id"):
        return payload.get("query") or "", None
    try:
        assignment_id, question_id = int(payload.get("assignment_id")), int(payload["question_id"])
    except (TypeError, ValueError) as exc:
        raise ValueError("错题问答需要有效的作业和题目编号") from exc
    context = _assignment_question_context(assignment_id, question_id, student_id)
    actions = {
        "why_wrong": "请分析我为什么会错，并讲清关键概念与容易混淆之处。",
        "explain": "请按学习步骤讲解这道题所用的知识点和思路。",
        "practice": "请基于这道题涉及的知识点，给出一道不含答案的同类练习题。",
    }
    instruction = actions.get(payload.get("action"), "请解释这道题涉及的知识点。")
    parts = [instruction, f"题目：{context['content']}", f"我的作答：{context['student_answer'] or '未作答'}"]
    if context.get("answer"):
        parts.append(f"参考答案：{context['answer']}")
    if context.get("analysis"):
        parts.append(f"已有解析：{context['analysis']}")
    return "\n".join(parts)[:2000], context


@router.post("/student/join-class")
def join_class(payload: dict,
               user: dict[str, Any] = Depends(require_student)) -> dict:
    """学生凭邀请码加入班级：{invite_code}。重复加入以最后一次为准。"""
    code = (payload.get("invite_code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="邀请码不能为空")
    stores = StoreBundle()
    cls = stores.classes.get_by_invite_code(code)
    if not cls:
        raise HTTPException(status_code=404, detail="邀请码无效或班级已停用")
    stores.classes.join(cls["id"], int(user["id"]))
    return {"status": "ok", "class": cls}


@router.get("/student/my-class")
def my_class(user: dict[str, Any] = Depends(require_student)) -> dict:
    """我的班级信息与同学名单（仅返回姓名/学号，保护隐私）。"""
    stores = StoreBundle()
    me = stores.users.get_by_username(user["username"])
    if not me or not me.get("class_id"):
        return {"class": None, "classmates": []}
    cls = stores.classes.get(int(me["class_id"]))
    classmates = stores.users.list_by_class(int(me["class_id"]))
    return {
        "class": cls,
        "classmates": [{"real_name": c.get("real_name"), "student_no": c.get("student_no")}
                       for c in classmates if c["id"] != user["id"]],
    }


@router.get("/student/question-taxonomy")
def student_question_taxonomy(
        _: dict[str, Any] = Depends(require_student)) -> dict:
    """学生作答控件所需的统一题型及 answer_mode。"""
    from edu_core.application.question_taxonomy import taxonomy_payload

    return taxonomy_payload()


@router.get("/student/assignments")
def list_assignments(user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    items = get_assignment_service().list_for_student(int(user["id"]))
    return {"items": items, "total": len(items)}


@router.get("/student/assignments/{assignment_id}")
def get_assignment(assignment_id: int,
                   user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().get_for_student(assignment_id, int(user["id"]))


@router.post("/student/assignments/{assignment_id}/submit")
def submit_assignment(assignment_id: int, payload: dict,
                      user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().submit(
        assignment_id, int(user["id"]), payload.get("answers"))


@router.get("/student/assignments/{assignment_id}/result")
def assignment_result(assignment_id: int,
                      user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_assignment_service

    return get_assignment_service().get_result(assignment_id, int(user["id"]))


# ---------------------------------------------------------------------------
# R3.1：错题本与自主练习。练习结果统一写入 answer_records。
# ---------------------------------------------------------------------------

@router.get("/student/wrong-book")
def wrong_book(subject: str = "", knowledge_point: str = "", include_resolved: bool = False,
               user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service

    items = get_learning_service().wrong_book(int(user["id"]), subject=subject.strip(),
                                               knowledge_point=knowledge_point.strip(), include_resolved=include_resolved)
    return {"items": items, "total": len(items)}


@router.get("/student/analytics/me")
def my_learning_profile(user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    return get_learning_service().profile(int(user["id"]))


@router.get("/student/analytics/recommendation-quality")
def recommendation_quality(user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    return get_learning_service().recommendation_quality(int(user["id"]))


@router.get("/student/recommendations")
def my_recommendations(limit: int = 10, user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    if not 1 <= limit <= 20:
        raise HTTPException(status_code=400, detail="limit 需在 1~20 之间")
    return get_learning_service().recommend(int(user["id"]), limit=limit)


@router.get("/student/consolidation/wrong/{question_id}")
def wrong_focus_plan(question_id: int, limit: int = 5, user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    try:
        return get_learning_service().wrong_focus(int(user["id"]), question_id, limit=max(1, min(limit, 10)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/student/consolidation/weak")
def weak_focus_plan(top_n: int = 3, per_point: int = 3,
                    user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    return get_learning_service().weak_focus(int(user["id"]), top_n=max(1, min(top_n, 5)),
                                              per_point=max(1, min(per_point, 5)))


@router.get("/student/consolidation/daily")
def daily_practice_plan(limit: int = 10, user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service
    return get_learning_service().daily_plan(int(user["id"]), limit=max(3, min(limit, 20)))


@router.post("/student/wrong-book/{question_id}/resolve")
def resolve_wrong(question_id: int, payload: dict, user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service

    try:
        get_learning_service().resolve_wrong(int(user["id"]), question_id,
                                              resolved=bool(payload.get("resolved", True)),
                                              note=(payload.get("note") or "").strip()[:512] or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/student/practice/questions")
def practice_questions(subject: str = "", knowledge_point: str = "", limit: int = 10,
                       user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service

    try:
        items = get_learning_service().practice_questions(int(user["id"]), subject=subject.strip(),
                                                           knowledge_point=knowledge_point.strip(), limit=int(limit))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "total": len(items)}


@router.post("/student/practice/submit")
def submit_practice(payload: dict, user: dict[str, Any] = Depends(require_student)) -> dict:
    from edu_core.application.factory import get_learning_service

    try:
        return get_learning_service().submit_practice(int(user["id"]), payload.get("answers"),
                                                       source=payload.get("source", "practice"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# R2.3：学生知识问答会话。答案与引用先由服务端生成、入库，再以 SSE 返回。
# ---------------------------------------------------------------------------

@router.get("/student/rag/sessions")
def rag_sessions(user: dict[str, Any] = Depends(require_student)) -> dict:
    return {"items": StoreBundle().rag.list_sessions(int(user["id"]), "student")}


@router.get("/student/rag/sessions/{session_id}/messages")
def rag_session_messages(session_id: int, user: dict[str, Any] = Depends(require_student)) -> dict:
    stores = StoreBundle()
    if not stores.rag.get_owned_session(session_id, int(user["id"]), "student"):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"items": stores.rag.messages(session_id, int(user["id"]))}


@router.get("/student/rag/assignment-question-context")
def rag_assignment_question_context(assignment_id: int, question_id: int,
                                    user: dict[str, Any] = Depends(require_student)) -> dict:
    try:
        return _assignment_question_context(assignment_id, question_id, int(user["id"]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/student/rag/sessions/{session_id}/clear")
def clear_rag_session(session_id: int, user: dict[str, Any] = Depends(require_student)) -> dict:
    if not StoreBundle().rag.clear_session(session_id, int(user["id"])):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "ok"}


@router.post("/student/rag/chat/stream")
def rag_chat_stream(payload: dict, user: dict[str, Any] = Depends(require_student)):
    """SSE 输出固定事件序列，浏览器可逐段渲染；绝不接受客户端过滤条件。"""
    from edu_core.config.settings import get_settings
    from edu_core.rag.conversations import RagConversationService
    from edu_core.rag.generation import RagAnswerService
    from edu_core.rag.retrieval import RagRetrievalService
    from edu_core.application.factory import get_learning_service

    stores, settings = StoreBundle(), get_settings()
    try:
        query, context = _chat_query_from_payload(payload, int(user["id"]))
        learning_action = payload.get("learning_action")
        if learning_action not in {None, "profile", "why_recommended", "study_priority"}:
            raise ValueError("不支持的学习助手动作")
        learning_context = get_learning_service().coaching_context(int(user["id"]))
        # 已提交作业的错题上下文可引用其答案；其他未提交作业题则从本地题库知识源排除。
        excluded_question_ids = set() if context else stores.assignments.pending_question_ids_for_student(int(user["id"]))
        result = RagConversationService(
            stores.rag, RagAnswerService(RagRetrievalService(stores.rag, settings), settings)
        ).ask(user_id=int(user["id"]), query=query,
              session_id=payload.get("session_id"), filters=_student_retrieval_filters(
                  user, context.get("subject") if context else None), learner_context=learning_context,
              learning_action=learning_action, socratic=bool(payload.get("socratic")),
              excluded_question_ids=excluded_question_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"问答服务暂不可用：{str(exc)[:160]}") from exc

    def events():
        yield _sse("session", {"session_id": result["session_id"], "message_id": result["message_id"],
                               "question_context": context})
        answer = result["answer"]
        for index in range(0, len(answer), 80):
            yield _sse("token", {"text": answer[index:index + 80]})
        yield _sse("citations", {"items": result["citations"]})
        yield _sse("done", {"refused": result["refused"], "reason": result["reason"],
                            "latency_ms": result["latency_ms"]})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/student/rag/messages/{message_id}/feedback")
def rag_feedback(message_id: int, payload: dict, user: dict[str, Any] = Depends(require_student)) -> dict:
    rating = payload.get("rating")
    stores = StoreBundle()
    if not stores.rag.owns_message(message_id, int(user["id"])):
        raise HTTPException(status_code=404, detail="回答不存在")
    try:
        stores.rag.feedback(message_id, int(user["id"]), int(rating),
                            (payload.get("correction") or "").strip()[:2000] or None)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="反馈评分仅支持 1 或 -1") from exc
    return {"status": "ok"}
