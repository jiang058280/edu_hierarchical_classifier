"""置信度分级测试。"""

import pytest

from edu_core.application.confidence import LOW, MEDIUM, HIGH, average_confidence, band, review_hint


def test_average_confidence():
    assert average_confidence({"subject": 0.9, "question_type": 0.8, "knowledge_point": 0.7}) == 0.8
    assert average_confidence({}) == 0.0


def test_band_boundaries():
    assert band(0.80) == HIGH
    assert band(0.7999) == MEDIUM
    assert band(0.60) == MEDIUM
    assert band(0.5999) == LOW
    with pytest.raises(AssertionError):
        assert band(0.5) == MEDIUM


def test_review_hint_mapping():
    assert "可直接采信" in review_hint(HIGH)
    assert "建议人工复核" in review_hint(MEDIUM)
    assert "必须人工复核" in review_hint(LOW)
    assert "必须人工复核" in review_hint("unknown")  # 兜底按低置信处理
