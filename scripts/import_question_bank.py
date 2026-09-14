"""将项目 ``knowledge_base/*.md`` 中的结构化题例导入业务题库。

脚本默认只预检；显式传入 ``--commit`` 才会写入数据库。重复执行时根据规范化题干
SHA-256 指纹跳过已存在题目，因此不会因重跑产生重复数据。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.application.question_taxonomy import is_type_allowed
from edu_core.application.grading import grade_objective, is_objective_type
from edu_core.storage.stores import get_engine, text_hash


QUESTION_HEADING = re.compile(r"^### 题目\s+(\d+)｜([^｜\r\n]+)｜难度\s*([1-5])\s*$", re.MULTILINE)
FIELD_HEADING = re.compile(r"^(?:- )?([^：\r\n]+)：\s*(.*)$")
OPTION_LINE = re.compile(r"^([A-Z])\s*[.．、:]\s*(.+)$", re.IGNORECASE)
EMBEDDED_OPTION = re.compile(
    r"(?:^|\s)([A-H])\s*[.．、:]\s*(.*?)(?=(?:\s+[A-H]\s*[.．、:]\s*)|$)",
    re.IGNORECASE | re.DOTALL,
)

EXPECTED_FIELDS = {
    "题目编号", "学段", "年级", "学科", "题型", "模型粗粒度题型", "一级知识点",
    "细粒度知识点", "难度", "认知层次", "建议作答时长", "题干", "选项", "参考答案",
    "解析", "评分要点", "标签",
}

TYPE_ALIASES = {
    "读图分析题": "读图题",
    "方程式题": "化学方程式题",
    "实验题": "实验探究题",
}


@dataclass(frozen=True)
class ParsedQuestion:
    content: str
    subject: str
    question_type: str
    knowledge_point: str
    options: list[dict] | None
    answer: str
    analysis: str
    difficulty: int
    grade_band: str
    grade: str
    external_id: str
    source_file: str
    source_question_no: int

    @property
    def fingerprint(self) -> str:
        return text_hash(self.content)


@dataclass
class ValidationIssue:
    file: str
    question_no: int | None
    message: str
    severity: str = "error"


def _clean_lines(lines: list[str]) -> str:
    """移除 Markdown 字段内容统一的两个空格缩进，保留段落和公式。"""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = [line[2:] if line.startswith("  ") else line for line in lines]
    return "\n".join(cleaned).strip()


def _parse_fields(block: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        match = FIELD_HEADING.match(line)
        if match and match.group(1).strip() in EXPECTED_FIELDS:
            current = match.group(1).strip()
            fields[current] = [match.group(2)] if match.group(2) else []
        elif current is not None:
            fields[current].append(line)
    return {key: _clean_lines(value) for key, value in fields.items()}


def _parse_options(value: str) -> list[dict] | None:
    if not value or value.strip() in {"无", "无。", "—", "-"}:
        return None
    options: list[dict] = []
    current: list[str] = []
    current_label = ""
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = OPTION_LINE.match(line)
        if match:
            if current:
                options.append({"key": current_label, "text": " ".join(current)})
            current_label = match.group(1).upper()
            current = [match.group(2).strip()]
        elif current:
            current.append(line)
    if current:
        options.append({"key": current_label, "text": " ".join(current)})
    return options or None


def _embedded_options(content: str) -> tuple[str, list[dict] | None]:
    """兼容少数把 A-D 选项写在题干中、并在选项字段标记“见题干”的资料。"""
    matches = list(EMBEDDED_OPTION.finditer(content))
    if len(matches) < 2 or matches[0].group(1).upper() != "A":
        return content, None
    labels = [match.group(1).upper() for match in matches]
    if labels != [chr(ord("A") + index) for index in range(len(labels))]:
        return content, None
    options = [{"key": match.group(1).upper(), "text": " ".join(match.group(2).split())}
               for match in matches]
    return content[:matches[0].start()].rstrip(), options


def _normalize_answer_type(question_type: str, answer: str,
                           options: list[dict] | None) -> tuple[str, str]:
    """将判断题答案归一为判分器支持的值；复合判断题改用文本作答。"""
    answer = answer.strip()
    if question_type != "判断题":
        return question_type, answer
    if re.search(r"[（(]\s*1\s*[）)]", answer):
        return "解答题", answer
    first = answer.rstrip("。．.!！ ")
    if first in {"正确", "对", "是", "错误", "错", "否"}:
        return question_type, first
    if answer[:1].upper() in {"A", "B"} and options:
        selected = answer[:1].upper()
        option = next((item.get("text", "") for item in options if item.get("key") == selected), "")
        if "正确" in option or option.endswith("对"):
            return question_type, "正确"
        if "错误" in option or option.endswith("错"):
            return question_type, "错误"
    if answer.startswith("正确"):
        return question_type, "正确"
    if answer.startswith("错误"):
        return question_type, "错误"
    # 多小题、T/F 序列等不能由单个判断控件表达，保留完整参考答案并按文本题作答。
    return "解答题", answer


def parse_markdown(path: Path) -> tuple[list[ParsedQuestion], list[ValidationIssue]]:
    content = path.read_text(encoding="utf-8-sig")
    matches = list(QUESTION_HEADING.finditer(content))
    questions: list[ParsedQuestion] = []
    issues: list[ValidationIssue] = []

    declared_match = re.search(r"^\| 题目总数 \|\s*(\d+)\s*\|", content, re.MULTILINE)
    if declared_match and int(declared_match.group(1)) != len(matches):
        issues.append(ValidationIssue(path.name, None,
                                      f"声明 {declared_match.group(1)} 题，实际识别 {len(matches)} 题"))

    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        fields = _parse_fields(content[match.end():end])
        missing = [name for name in ("题目编号", "学段", "年级", "学科", "题型", "题干", "参考答案", "解析")
                   if not fields.get(name)]
        if missing:
            issues.append(ValidationIssue(path.name, number, f"缺少字段：{', '.join(missing)}"))
            continue

        raw_type = fields["题型"].strip()
        heading_type = match.group(2).strip()
        if raw_type != heading_type:
            issues.append(ValidationIssue(path.name, number,
                                          f"标题题型 {heading_type} 与字段题型 {raw_type} 不一致"))
        subject = fields["学科"].strip()
        question_type = TYPE_ALIASES.get(raw_type, raw_type)
        if subject == "物理" and question_type == "简答题":
            question_type = "解答题"
        try:
            difficulty = int(fields.get("难度") or match.group(3))
        except ValueError:
            issues.append(ValidationIssue(path.name, number, f"难度不是整数：{fields.get('难度')}"))
            continue
        if not 1 <= difficulty <= 5:
            issues.append(ValidationIssue(path.name, number, f"难度超出 1~5：{difficulty}"))
            continue

        question_content = fields["题干"].strip()
        options_value = fields.get("选项", "")
        options = _parse_options(options_value)
        if not options and options_value.strip() in {"见题干", "详见题干"}:
            question_content, options = _embedded_options(question_content)
        question_type, normalized_answer = _normalize_answer_type(
            question_type, fields["参考答案"], options)
        if not is_type_allowed(subject, question_type):
            issues.append(ValidationIssue(path.name, number,
                                          f"系统题型目录不支持：{subject}/{question_type}"))
            continue
        if question_type in {"选择题", "单选题", "多选题"} and (not options or len(options) < 2):
            issues.append(ValidationIssue(path.name, number, "选择题未识别到至少两个选项"))
            continue
        if is_objective_type(question_type):
            try:
                # 用参考答案判自身，检查选择字母或判断值能否被正式判分规则识别。
                grade_objective(normalized_answer, normalized_answer, question_type)
            except ValueError as exc:
                issues.append(ValidationIssue(path.name, number, f"客观题答案不可自动判分：{exc}"))
                continue

        grade_band = fields["学段"].strip()
        grade = fields["年级"].strip()
        knowledge = (fields.get("细粒度知识点") or fields.get("一级知识点") or "").strip()
        questions.append(ParsedQuestion(
            content=question_content,
            subject=subject,
            question_type=question_type,
            knowledge_point=knowledge[:128],
            options=options,
            answer=normalized_answer,
            analysis=fields["解析"].strip(),
            difficulty=difficulty,
            grade_band=grade_band,
            grade=grade,
            external_id=fields["题目编号"].strip(),
            source_file=path.name,
            source_question_no=number,
        ))
    return questions, issues


def load_bank(source_dir: Path) -> tuple[list[ParsedQuestion], list[ValidationIssue], list[Path]]:
    files = sorted(source_dir.glob("*.md"))
    all_questions: list[ParsedQuestion] = []
    all_issues: list[ValidationIssue] = []
    for path in files:
        questions, issues = parse_markdown(path)
        all_questions.extend(questions)
        all_issues.extend(issues)

    fingerprints: dict[str, ParsedQuestion] = {}
    unique: list[ParsedQuestion] = []
    for question in all_questions:
        previous = fingerprints.get(question.fingerprint)
        if previous:
            all_issues.append(ValidationIssue(
                question.source_file, question.source_question_no,
                f"题干与 {previous.source_file} 第 {previous.source_question_no} 题重复",
                severity="warning",
            ))
            continue
        fingerprints[question.fingerprint] = question
        unique.append(question)
    return unique, all_issues, files


def _teacher_id(engine, username: str) -> int:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE username=:username AND role IN ('teacher','admin') LIMIT 1"
        ), {"username": username}).first()
    if not row:
        raise RuntimeError(f"数据库中不存在教师或管理员账号：{username}")
    return int(row[0])


def import_questions(questions: list[ParsedQuestion], username: str) -> tuple[int, int, list[int]]:
    engine = get_engine()
    teacher_id = _teacher_id(engine, username)
    hashes = [question.fingerprint for question in questions]
    existing: set[str] = set()
    # 避免构造超长 IN 子句，分批读取已有指纹。
    with engine.connect() as conn:
        for start in range(0, len(hashes), 500):
            chunk = hashes[start:start + 500]
            binds = ",".join(f":h{i}" for i in range(len(chunk)))
            params = {f"h{i}": value for i, value in enumerate(chunk)}
            existing.update(str(row[0]) for row in conn.execute(
                text(f"SELECT text_hash FROM questions WHERE text_hash IN ({binds})"), params
            ).all())

    inserted_ids: list[int] = []
    skipped = 0
    statement = text("""
        INSERT INTO questions (
            content, subject, question_type, knowledge_point, source, options_json, answer,
            analysis, difficulty, grade_band, grade, knowledge_node_id, created_by, text_hash, status
        ) VALUES (
            :content, :subject, :question_type, :knowledge_point, 'knowledge_base_md', :options_json,
            :answer, :analysis, :difficulty, :grade_band, :grade, NULL, :created_by, :text_hash, 'published'
        )
    """)
    # 单个事务：任何一题写入失败，整批回滚，避免半批导入。
    with engine.begin() as conn:
        for question in questions:
            if question.fingerprint in existing:
                skipped += 1
                continue
            result = conn.execute(statement, {
                "content": question.content,
                "subject": question.subject,
                "question_type": question.question_type,
                "knowledge_point": question.knowledge_point,
                "options_json": json.dumps(question.options, ensure_ascii=False) if question.options else None,
                "answer": question.answer,
                "analysis": question.analysis,
                "difficulty": question.difficulty,
                "grade_band": question.grade_band,
                "grade": question.grade,
                "created_by": teacher_id,
                "text_hash": question.fingerprint,
            })
            inserted_ids.append(int(result.lastrowid))
    return len(inserted_ids), skipped, inserted_ids


def _summary(questions: list[ParsedQuestion]) -> dict:
    return {
        "total": len(questions),
        "by_subject": dict(sorted(Counter(item.subject for item in questions).items())),
        "by_grade_band": dict(sorted(Counter(item.grade_band for item in questions).items())),
        "by_question_type": dict(sorted(Counter(item.question_type for item in questions).items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="预检并导入 knowledge_base Markdown 题库")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "knowledge_base")
    parser.add_argument("--teacher", default="lgq", help="归属教师用户名（默认 lgq）")
    parser.add_argument("--commit", action="store_true", help="通过预检后实际写入数据库")
    parser.add_argument("--report", type=Path, help="JSON 报告输出路径")
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    if not source_dir.is_dir():
        print(f"ERROR: 题库目录不存在：{source_dir}")
        return 2

    questions, issues, files = load_bank(source_dir)
    result = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "commit" if args.commit else "dry-run",
        "source_dir": str(source_dir),
        "file_count": len(files),
        "summary": _summary(questions),
        "issues": [asdict(issue) for issue in issues],
        "inserted": 0,
        "skipped_existing": 0,
        "inserted_id_min": None,
        "inserted_id_max": None,
    }

    print(json.dumps({key: value for key, value in result.items() if key != "issues"},
                     ensure_ascii=False, indent=2))
    errors = [issue for issue in issues if issue.severity == "error"]
    warnings = [issue for issue in issues if issue.severity == "warning"]
    if errors:
        print(f"ERROR: 预检发现 {len(errors)} 个错误，未写入数据库。")
        for issue in issues[:30]:
            print(f"- [{issue.severity}] {issue.file} / 第 {issue.question_no or '-'} 题：{issue.message}")
        if len(issues) > 30:
            print(f"- 其余 {len(issues) - 30} 个问题请查看 JSON 报告")
    elif warnings:
        print(f"预检通过；发现并跳过 {len(warnings)} 道来源内重复题。")

    if args.commit and not errors:
        inserted, skipped, ids = import_questions(questions, args.teacher)
        result["inserted"] = inserted
        result["skipped_existing"] = skipped
        result["inserted_id_min"] = min(ids) if ids else None
        result["inserted_id_max"] = max(ids) if ids else None
        print(f"导入完成：新增 {inserted} 题，跳过数据库已有 {skipped} 题。")

    report = args.report or ROOT / "reports" / "imports" / "knowledge_base_questions_latest.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
