"""教师端路由组（平台计划 M0/M1，require_teacher 门禁）。

M0：班级管理（建班/名单/邀请码）。
M1：题库完整 CRUD 与筛选、AI 预标注（单/批）、组卷生成/保存/Word 导出。
"""

from __future__ import annotations

import secrets
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from edu_core.security.auth import require_teacher
from edu_core.storage.stores import StoreBundle

router = APIRouter()


def _stores() -> StoreBundle:
    return StoreBundle()


# ---------------------------------------------------------------------------
# 班级管理（M0）
# ---------------------------------------------------------------------------

@router.get("/teacher/classes")
def my_classes(user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """我创建的班级列表（含学生数）。"""
    classes = _stores().classes.list_by_teacher(int(user["id"]))
    return {"classes": classes}


@router.post("/teacher/classes")
def create_class(payload: dict, user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """创建班级：{name, grade_band, grade} → 生成 6 位邀请码。"""
    name = (payload.get("name") or "").strip()
    grade_band = (payload.get("grade_band") or "").strip()
    grade = (payload.get("grade") or "").strip()
    if not name or grade_band not in ("初中", "高中") or not grade:
        raise HTTPException(
            status_code=400, detail="name / grade(年级) 必填，grade_band 仅支持 初中/高中")
    invite_code = secrets.token_hex(3).upper()  # 6 位十六进制邀请码
    stores = _stores()
    try:
        class_id = stores.classes.create(
            name, grade_band, grade, created_by=int(user["id"]), invite_code=invite_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    stores.audit.insert(
        action="create_class", user_id=user.get("id") or None,
        username=user.get("username"), resource=f"classes/{class_id}",
        detail={"name": name, "grade_band": grade_band, "grade": grade})
    return {"status": "ok", "id": class_id, "invite_code": invite_code}


@router.get("/teacher/classes/{class_id}/students")
def class_students(class_id: int, user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """班级学生名册（仅班级创建教师可看）。"""
    stores = _stores()
    cls = stores.classes.get(class_id)
    if not cls or cls["created_by"] != int(user["id"]):
        raise HTTPException(status_code=404, detail="班级不存在")
    return {"class": cls, "students": stores.users.list_by_class(class_id)}


# ---------------------------------------------------------------------------
# 题库管理（M1）：完整题目 CRUD 与筛选
# ---------------------------------------------------------------------------

@router.get("/teacher/knowledge-nodes")
def knowledge_nodes(subject: str = "", level: int | None = None,
                    _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """知识点树（录入页下拉与组卷筛选使用）。"""
    nodes = _stores().knowledge_nodes.list(subject=subject, level=level)
    return {"total": len(nodes), "nodes": nodes}


@router.get("/teacher/questions")
def list_questions(subject: str = "", question_type: str = "", keyword: str = "",
                   grade_band: str = "", difficulty: int | None = None,
                   status: str = "", knowledge: str = "",
                   limit: int = 50, offset: int = 0,
                   _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """题库筛选列表（学段/学科/题型/难度/状态/知识点/关键词）。"""
    items, total = _stores().questions.list(
        subject=subject, question_type=question_type, keyword=keyword,
        grade_band=grade_band, difficulty=difficulty, status=status,
        knowledge=knowledge, limit=min(limit, 200), offset=max(offset, 0))
    return {"total": total, "items": items}


@router.get("/teacher/questions/{question_id}")
def get_question(question_id: int,
                 _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """题目完整信息。"""
    question = _stores().questions.get(question_id)
    if not question:
        raise HTTPException(status_code=404, detail="题目不存在")
    return question


def _full_fields(payload: dict, *, require_content: bool = True) -> dict:
    fields: dict[str, Any] = {}
    if require_content or payload.get("text"):
        text = (payload.get("text") or "").strip()
        if require_content and len(text) < 10:
            raise HTTPException(status_code=400, detail="题干过短（至少 10 字符）")
        fields["content"] = text
    for key in ("subject", "question_type", "knowledge_point", "answer",
                "analysis", "grade_band", "grade"):
        if key in payload:
            value = payload.get(key)
            fields[key] = (value or "").strip() if isinstance(value, str) else value
    if payload.get("difficulty") is not None:
        difficulty = int(payload["difficulty"])
        if not 1 <= difficulty <= 5:
            raise HTTPException(status_code=400, detail="difficulty 取值 1~5")
        fields["difficulty"] = difficulty
    if payload.get("options") is not None:
        options = payload["options"]
        if options and not isinstance(options, list):
            raise HTTPException(status_code=400, detail="options 应为 [{key,text}] 数组")
        fields["options"] = options
    if payload.get("knowledge_node_id") is not None:
        fields["knowledge_node_id"] = int(payload["knowledge_node_id"])
    if payload.get("status"):
        if payload["status"] not in ("draft", "pending", "published"):
            raise HTTPException(status_code=400, detail="status 取值 draft/pending/published")
        fields["status"] = payload["status"]
    return fields


@router.post("/teacher/questions")
def create_question(payload: dict,
                    user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """录入完整题目（AI 确认流保存入口）：题干/选项/答案/解析/难度/学段/知识点。"""
    fields = _full_fields(payload)
    stores = _stores()
    question_id = stores.questions.insert(
        fields["content"], fields.get("subject", ""),
        question_type=fields.get("question_type", ""),
        knowledge_point=fields.get("knowledge_point", ""),
        source="teacher", options=fields.get("options"),
        answer=fields.get("answer"), analysis=fields.get("analysis"),
        difficulty=fields.get("difficulty"), grade_band=fields.get("grade_band"),
        grade=fields.get("grade"), knowledge_node_id=fields.get("knowledge_node_id"),
        created_by=int(user["id"]), )
    return {"status": "ok", "id": question_id}


@router.put("/teacher/questions/{question_id}")
def update_question(question_id: int, payload: dict,
                    _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """编辑题目（部分字段即可）。"""
    fields = _full_fields(payload, require_content=False)
    if not _stores().questions.update(question_id, **fields):
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# AI 预标注（M1 确认流）：只推理不落库，教师确认后走 POST /teacher/questions
# ---------------------------------------------------------------------------

def _preannotate(text: str, service) -> dict:
    raw = service.predictor.predict(text)
    return {
        "text": text,
        "subject": raw["subject"],
        "question_type": raw["question_type"],
        "knowledge_point": raw["knowledge_point"],
        "grade_band": raw.get("grade_band"),
        "confidences": raw["confidence"],
    }


@router.post("/teacher/questions/classify")
def classify_one(payload: dict,
                 _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """单条 AI 预标注。"""
    text = (payload.get("text") or "").strip()
    if len(text) < 10:
        raise HTTPException(status_code=400, detail="题干过短（至少 10 字符）")
    return _preannotate(text, _stores_service())


@router.post("/teacher/questions/classify-batch")
def classify_batch(payload: dict,
                   _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """批量 AI 预标注（前端智能切分后调用，上限 50 条）。"""
    texts = payload.get("texts") or []
    if not texts or len(texts) > 50:
        raise HTTPException(status_code=400, detail="texts 需为 1~50 条")
    service = _stores_service()
    results = [_preannotate((t or "").strip(), service) for t in texts if len((t or "").strip()) >= 10]
    return {"items": results}


def _stores_service():
    from edu_core.application.factory import get_classification_service
    return get_classification_service()


# ---------------------------------------------------------------------------
# 组卷与试卷库（M1）
# ---------------------------------------------------------------------------

@router.post("/teacher/papers/generate")
def generate_paper(payload: dict,
                   _: dict[str, Any] = Depends(require_teacher)) -> dict:
    """配比组卷：{subject, grade_band?, type_counts:{选择题:n,...}, knowledge?, difficulty?}。

    规则式从题库（published）按各题型配额随机抽题。
    """
    subject = (payload.get("subject") or "").strip()
    if not subject:
        raise HTTPException(status_code=400, detail="subject 必填")
    grade_band = (payload.get("grade_band") or "").strip()
    knowledge = (payload.get("knowledge") or "").strip()
    difficulty = payload.get("difficulty")
    type_counts = payload.get("type_counts") or {}
    if not type_counts or sum(int(v) for v in type_counts.values()) < 1:
        raise HTTPException(status_code=400, detail="type_counts 至少配置一种题型的数量")
    stores = _stores()
    picked: list[dict] = []
    import random

    rng = random.Random()
    for qtype, want in type_counts.items():
        want = int(want)
        if want < 1:
            continue
        items, _total = stores.questions.list(
            subject=subject, question_type=qtype, knowledge=knowledge,
            grade_band=grade_band, difficulty=difficulty,
            status="published", limit=500)
        if not items:
            continue
        picked.extend(rng.sample(items, min(want, len(items))))
    if not picked:
        raise HTTPException(status_code=404, detail="题库中没有匹配的题目，请调整筛选条件")
    return {"questions": picked, "total": len(picked)}


@router.post("/teacher/papers")
def save_paper(payload: dict, user: dict[str, Any] = Depends(require_teacher)) -> dict:
    """保存试卷：{title, subject, grade_band, question_ids:[...]}。"""
    title = (payload.get("title") or "").strip()
    subject = (payload.get("subject") or "").strip()
    grade_band = (payload.get("grade_band") or "高中").strip()
    question_ids = payload.get("question_ids") or []
    if len(set(int(q) for q in question_ids)) != len(question_ids):
        raise HTTPException(status_code=400, detail="question_ids 存在重复题目")
    if not title or not question_ids:
        raise HTTPException(status_code=400, detail="title 与 question_ids 必填")
    stores = _stores()
    paper_id = stores.papers.create(title, subject, grade_band,
                                    created_by=int(user["id"]),
                                    question_ids=[int(q) for q in question_ids])
    return {"status": "ok", "id": paper_id}


@router.get("/teacher/papers")
def list_papers(limit: int = 50, offset: int = 0,
                user: dict[str, Any] = Depends(require_teacher)) -> dict:
    items, total = _stores().papers.list(created_by=int(user["id"]),
                                         limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.get("/teacher/papers/{paper_id}")
def get_paper(paper_id: int, _: dict[str, Any] = Depends(require_teacher)) -> dict:
    paper = _stores().papers.get(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return paper


@router.delete("/teacher/papers/{paper_id}")
def delete_paper(paper_id: int, _: dict[str, Any] = Depends(require_teacher)) -> dict:
    if not _stores().papers.delete(paper_id):
        raise HTTPException(status_code=404, detail="试卷不存在")
    return {"status": "ok"}


@router.get("/teacher/papers/{paper_id}/export")
def export_paper(paper_id: int, _: dict[str, Any] = Depends(require_teacher)) -> Response:
    """导出 Word 打印版：第一页试卷，第二页参考答案与解析。"""
    from docx import Document

    paper = _stores().papers.get(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="试卷不存在")
    doc = Document()
    doc.add_heading(paper["title"], level=0)
    doc.add_paragraph(f"学科：{paper['subject']}    学段：{paper['grade_band']}    "
                      f"共 {len(paper['questions'])} 题")
    for i, q in enumerate(paper["questions"], start=1):
        doc.add_paragraph(f"{i}.（{q['question_type']}）{q['content']}")
        if q.get("options"):
            for opt in q["options"]:
                doc.add_paragraph(f"    {opt.get('key')}. {opt.get('text')}")
    doc.add_page_break()
    doc.add_heading("参考答案与解析", level=1)
    for i, q in enumerate(paper["questions"], start=1):
        line = f"{i}. 答案：{q.get('answer') or '—'}"
        if q.get("knowledge_point"):
            line += f"    知识点：{q['knowledge_point']}"
        if q.get("difficulty"):
            line += f"    难度：{q['difficulty']}"
        doc.add_paragraph(line)
        if q.get("analysis"):
            doc.add_paragraph(f"    解析：{q['analysis']}")

    buf = BytesIO()
    doc.save(buf)
    from urllib.parse import quote

    filename = paper["title"].replace(" ", "_") + ".docx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename=\"paper_{paper_id}.docx\"; "
                 f"filename*=UTF-8''{quote(filename)}"})
