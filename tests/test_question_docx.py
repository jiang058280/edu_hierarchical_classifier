"""F2 DOCX 题目提取与切分测试；多格式（PDF/MD/TXT）经 parse_document_questions 分派。"""

from io import BytesIO

import pytest
from docx import Document

from edu_core.application.question_docx import parse_document_questions, parse_docx_questions, split_questions


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


def test_markdown_and_txt_files_split_with_same_rule():
    markdown = "# 题目练习\n\n1. 第一道数学题目的完整题干\nA. 甲\nB. 乙\n\n2. 第二道数学题目的完整题干\n".encode("utf-8")
    assert len(parse_document_questions("练习.md", markdown)) == 2
    txt = "（1）第一道语文题目的完整题干\n（2）第二道语文题目的完整题干\n".encode("utf-8")
    assert len(parse_document_questions("练习.txt", txt)) == 2


def test_docx_still_routes_through_docx_parser():
    payload = _docx(["1、第一道测试题目的完整题干", "2、第二道测试题目的完整题干"])
    assert len(parse_document_questions("题目.docx", payload)) == 2


def test_pdf_text_layer_splits_questions(monkeypatch):
    import pypdf

    page = type("Page", (), {"extract_text": lambda self: "1. 第一道物理题目的完整题干\nA. 甲\nB. 乙"})()
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("Reader", (), {"pages": [page]})())
    questions = parse_document_questions("试卷.pdf", b"fake-pdf-bytes")
    assert len(questions) == 1 and "物理题目" in questions[0]


def test_scanned_pdf_without_ocr_returns_actionable_error(monkeypatch):
    import pypdf

    page = type("Page", (), {"extract_text": lambda self: ""})()
    monkeypatch.setattr(pypdf, "PdfReader", lambda _: type("Reader", (), {"pages": [page]})())
    with pytest.raises(ValueError, match="OCR"):
        parse_document_questions("扫描件.pdf", b"fake-pdf-bytes")


def test_unsupported_suffix_rejected():
    with pytest.raises(ValueError, match="仅支持"):
        parse_document_questions("题目.doc", b"payload")
