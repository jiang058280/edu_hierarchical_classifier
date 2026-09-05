"""API 请求/响应模型（Pydantic v2）。

契约说明：
- 字段命名兼容旧前端（ai_input.html / platform.html 使用的 text / q_type / correct 等），
  新字段（classification_id / band / review_hint 等）为增量，旧前端可平滑迁移；
- 输入校验在此层完成（文本长度上限、枚举、数量范围），业务层不重复校验。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    """POST /api/v1/classify 请求体。"""

    text: str = Field(min_length=1, max_length=4000, description="题目文本")


class FeedbackRequest(BaseModel):
    """POST /api/v1/feedback 请求体（字段兼容旧版 /api/feedback）。"""

    classification_id: int | None = Field(default=None, ge=1, description="关联的推理留痕 id")
    text: str | None = Field(default=None, max_length=4000, description="题目文本（兼容旧字段）")
    subject: str | None = Field(default=None, max_length=64)
    correct: bool = Field(description="模型结果是否正确")
    corrected_subject: str | None = Field(default=None, max_length=64)
    corrected_type: str | None = Field(default=None, max_length=64)
    corrected_knowledge: str | None = Field(default=None, max_length=128)
    comment: str | None = Field(default=None, max_length=1024)


class SaveQuestionRequest(BaseModel):
    """POST /api/v1/questions 请求体（字段兼容旧版 /api/save_question）。"""

    text: str = Field(min_length=1, max_length=4000)
    subject: str = Field(default="", max_length=64)
    q_type: str = Field(default="", max_length=64)
    knowledge: str = Field(default="", max_length=128)
    source: str = Field(default="manual", max_length=64)


class GeneratePaperRequest(BaseModel):
    """POST /api/v1/papers/generate 请求体（兼容旧版 /api/generate_paper）。"""

    subjects: list[str] = Field(default_factory=list, max_length=16)
    types: list[str] = Field(default_factory=list, max_length=16)
    count: int = Field(default=10, ge=1, le=100)


class ActivateModelRequest(BaseModel):
    """POST /api/v1/models/{version}/activate 请求体。"""

    validate_files: bool = Field(default=True, description="激活前是否校验版本目录文件齐全")
