"""F2 DOCX 题目提取与切分测试。"""

from io import BytesIO

import pytest
from docx import Document

from edu_core.application.question_docx import parse_docx_questions, split_questions


def _docx(lines: list[str]) -> bytes:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_standard_numbers_do_not_split_subquestions():
    lines = [
        "1. 阅读材料并回答下列问题。", "（1）概括材料内容。", "（2）说明历史意义。",
        "2. 下列说法正确的是（　　）", "A. 甲", "B. 乙",
    ]
    questions = split_questions(lines)
    assert len(questions) == 2
    assert "（2）说明历史意义" in questions[0]


def test_bracket_numbering_supported_when_no_standard_numbers():
    questions = split_questions([
        "（1）第一道题目的文字足够长", "A. 选项甲", "B. 选项乙",
        "（2）第二道题目的文字也足够长", "A. 正确", "B. 错误",
    ])
    assert len(questions) == 2


def test_docx_roundtrip_and_invalid_payload():
    payload = _docx(["1、第一道测试题目的完整题干", "答案：A", "2、第二道测试题目的完整题干"])
    questions = parse_docx_questions(payload)
    assert len(questions) == 2
    with pytest.raises(ValueError, match="有效的 DOCX"):
        parse_docx_questions(b"not-a-docx")


def test_max_question_limit():
    lines = [f"{index}. 第 {index} 道题目的完整内容" for index in range(1, 5)]
    with pytest.raises(ValueError, match="单次最多 3 题"):
        split_questions(lines, max_questions=3)
