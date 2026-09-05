"""API 请求模型校验测试（不依赖数据库与模型）。"""

import pytest
from pydantic import ValidationError

from edu_core.api.schemas import (
    ClassifyRequest, FeedbackRequest, GeneratePaperRequest, SaveQuestionRequest,
)


def test_classify_request_length_limit():
    assert ClassifyRequest(text="题目").text == "题目"
    with pytest.raises(ValidationError):
        ClassifyRequest(text="")
    with pytest.raises(ValidationError):
        ClassifyRequest(text="长" * 4001)


def test_feedback_request_requires_correct_flag():
    fb = FeedbackRequest(correct=True, classification_id=1)
    assert fb.correct is True and fb.classification_id == 1
    with pytest.raises(ValidationError):
        FeedbackRequest()  # correct 必填
    with pytest.raises(ValidationError):
        FeedbackRequest(correct=False, classification_id=0)  # ge=1


def test_save_question_request_defaults():
    q = SaveQuestionRequest(text="题目")
    assert q.subject == "" and q.q_type == "" and q.source == "manual"
    with pytest.raises(ValidationError):
        SaveQuestionRequest(text="x" * 4001)


def test_generate_paper_request_range():
    assert GeneratePaperRequest(count=10).count == 10
    with pytest.raises(ValidationError):
        GeneratePaperRequest(count=0)
    with pytest.raises(ValidationError):
        GeneratePaperRequest(count=101)
