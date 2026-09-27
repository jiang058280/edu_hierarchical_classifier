from scripts.build_rag_review_draft import build_cases, review_markdown, select_questions
from scripts.import_question_bank import ParsedQuestion
from scripts.evaluate_rag import ROOT, dataset_label


def test_review_draft_preserves_provenance_and_never_marks_ready(tmp_path):
    source = tmp_path / "example.md"
    source.write_text("# 教学资料\n### 题目 1｜单选题｜难度 1\n正文", encoding="utf-8")
    question = ParsedQuestion("哪个正确？", "数学", "单选题", "代数",
                              [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
                              "A", "原资料解析", 1, "初中", "初一", "EX-1", source.name, 1)
    assert select_questions([question, question]) == [question]
    cases = build_cases([question], tmp_path)
    assert len(cases) == 5
    assert len({case["id"] for case in cases}) == 5
    assert all(case["ready"] is False and not case["expected_chunk_ids"] for case in cases)
    assert all(case["source_line"] == 2 and len(case["source_sha256"]) == 64 for case in cases)
    assert "A. 甲" in cases[0]["query"]
    assert cases[2]["conversation_history"][1]["content"].endswith("原资料解析")
    assert cases[4]["filters"]["grade_band"] == "高中"
    assert "grade" not in cases[4]["filters"]
    assert "不能直接认定应拒答" in review_markdown(cases)


def test_dataset_label_accepts_relative_and_external_paths(tmp_path):
    from pathlib import Path

    assert dataset_label(Path("eval_sets/rag_baseline.jsonl")) == str(Path("eval_sets/rag_baseline.jsonl"))
    external = tmp_path / "external.jsonl"
    expected = str(external.relative_to(ROOT)) if external.is_relative_to(ROOT) else str(external)
    assert dataset_label(external) == expected
