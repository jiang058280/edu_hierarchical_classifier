"""Markdown 题库导入器的结构化解析回归测试。"""

from pathlib import Path

from scripts.import_question_bank import load_bank, parse_markdown


def _document(question: str, total: int = 1) -> str:
    return f"""# 测试资料

| 项目 | 内容 |
| --- | --- |
| 题目总数 | {total} |

{question}
"""


def test_parse_embedded_options_and_normalize_judgment(tmp_path: Path):
    path = tmp_path / "题库.md"
    path.write_text(_document("""### 题目 1｜判断题｜难度 2
- 题目编号：TEST-001
- 学段：初中
- 年级：初一
- 学科：地理
- 题型：判断题
- 难度：2
- 题干：
  地球自西向东转动。（　）
- 选项：
  A. 正确
  B. 错误
- 参考答案：
  A
- 解析：
  地球自转方向是自西向东。
"""), encoding="utf-8")

    questions, issues = parse_markdown(path)

    assert not issues
    assert len(questions) == 1
    assert questions[0].options == [
        {"key": "A", "text": "正确"}, {"key": "B", "text": "错误"},
    ]
    assert questions[0].answer == "正确"


def test_parse_missing_list_marker_and_deduplicate_stems(tmp_path: Path):
    question = """### 题目 1｜材料分析题｜难度 3
- 题目编号：TEST-002
- 学段：高中
- 年级：高一
- 学科：历史
- 题型：材料分析题
- 难度：3
- 题干：
  阅读材料并说明其历史意义。
- 选项：
  无
  参考答案：
  示例答案。
- 解析：
  示例解析。
"""
    (tmp_path / "甲.md").write_text(_document(question), encoding="utf-8")
    (tmp_path / "乙.md").write_text(_document(question), encoding="utf-8")

    questions, issues, files = load_bank(tmp_path)

    assert len(files) == 2
    assert len(questions) == 1
    assert questions[0].answer == "示例答案。"
    assert len(issues) == 1
    assert issues[0].severity == "warning"
