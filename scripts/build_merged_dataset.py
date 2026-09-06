"""构建 v0.3 合并训练数据集（多源教育题库扩充，改进计划数据扩充轮）。

数据源 → 统一 schema（text/subject/question_type/knowledge_point/grade_band/source）：

  1. CMMLU  test/high_school_*.csv      6 学科 836 条   选择题  高中（答案 A-D）
  2. M3KE   test/{Subject}-{Cat}-{Level}.jsonl  8 学科  选择题  初中全量 2,167 + 高中采样 1,000
  3. GAOKAO-Bench  Objective *_MCQs（选择题）+ 理科政史地 Subjective（解答题）        高中真题
  4. NuminaMath-CoT cn_k12 子集采样      数学解答题（排除选项题与填空题）  学段未知

规则：
  - 现有 train/val/test 三份 CSV 保持各自划分不变（test 为固定回归基准，绝不动）；
  - 新增数据按 (source, subject) 分层 85/15 并入 train/val；
  - 新增数据 knowledge_point 置空（训练时按 -100 掩码，知识头只用原有标注）；
  - labels_source 统一记 rule（非人工）；grade_band 空串 = 学段未知（掩码）；
  - 全量按归一化文本去重（对既有 train/val/test 与新增内部都查重）；
  - labels.json 增加 grade_bands 列表并更新 stats。

运行：venv\\Scripts\\python scripts\\build_merged_dataset.py
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys as _sys
from collections import defaultdict
from pathlib import Path as _Path

for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings

logger = get_logger(__name__)

RANDOM_SEED = 42
VAL_RATIO = 0.15
M3KE_SENIOR_CAP = 1000
NUMINA_CAP = 1500
GAOKAO_SUBJECTIVE_CAP = 500
MAX_TEXT_CHARS = 1200

CSV_FIELDS = ["text", "subject", "question_type", "knowledge_point",
              "raw_question_type", "answer", "labels_source", "grade_band", "source"]

SUBJECT_ZH = {
    "Biology": "生物", "Chemistry": "化学", "Chinese": "语文", "Geography": "地理",
    "History": "历史", "Math": "数学", "Physics": "物理", "Politics": "政治",
    "Math_I": "数学", "Math_II": "数学", "Political_Science": "政治",
    "English": "英语",
}
LEVEL_ZH = {"Junior high school": "初中", "High school": "高中", "High schoo": "高中"}
OPTION_KEYS = ("A", "B", "C", "D")


def norm_text(text: str) -> str:
    return "".join((text or "").split())


def mcq_text(question: str, options: dict[str, str]) -> str:
    parts = [question.strip()]
    for key in OPTION_KEYS:
        val = (options.get(key) or "").strip()
        if val:
            parts.append(f"{key}. {val}")
    return "\n".join(p for p in parts if p)[:MAX_TEXT_CHARS]


def make_row(text: str, subject: str, question_type: str, grade_band: str,
             source: str, raw_type: str = "", answer: str = "") -> dict:
    return {
        "text": text[:MAX_TEXT_CHARS],
        "subject": subject,
        "question_type": question_type,
        "knowledge_point": "",
        "raw_question_type": raw_type,
        "answer": (answer or "")[:512],
        "labels_source": "rule",
        "grade_band": grade_band,
        "source": source,
    }


CMMLU_SUBJECT = {
    "biology": "生物", "chemistry": "化学", "geography": "地理",
    "mathematics": "数学", "physics": "物理", "politics": "政治",
}


def load_cmmlu(root) -> list[dict]:
    rows = []
    base = root / "data" / "raw" / "_cmmlu_tmp" / "CMMLU-master" / "data" / "test"
    for f in sorted(base.glob("high_school_*.csv")):
        subject = CMMLU_SUBJECT.get(f.stem.replace("high_school_", ""))
        if not subject:
            continue
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                text = mcq_text(r["Question"], {k: r.get(k, "") for k in OPTION_KEYS})
                if len(text) < 15:
                    continue
                rows.append(make_row(text, subject, "选择题", "高中", "cmmlu",
                                     raw_type="单选题", answer=r.get("Answer", "")))
    return rows


def load_m3ke(root, senior_cap: int) -> list[dict]:
    rows = []
    base = root / "data" / "raw" / "_m3ke_tmp" / "M3KE-main" / "data" / "test"
    senior_pool: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(base.glob("*.jsonl")):
        parts = f.stem.split("-")
        if len(parts) < 3:
            continue
        subject = SUBJECT_ZH.get(parts[0].strip())
        level = LEVEL_ZH.get(parts[-1].strip())
        if not subject or not level:
            continue
        for ln in open(f, encoding="utf-8"):
            if not ln.strip():
                continue
            item = json.loads(ln)
            text = mcq_text(item["question"], {k: item.get(k, "") for k in OPTION_KEYS})
            if len(text) < 15:
                continue
            row = make_row(text, subject, "选择题", level, "m3ke",
                           raw_type="单选题", answer=item.get("answer", ""))
            if level == "初中":
                rows.append(row)
            else:
                senior_pool[subject].append(row)
    rng = random.Random(RANDOM_SEED)
    kept = 0
    for subject in sorted(senior_pool):
        pool = senior_pool[subject]
        rng.shuffle(pool)
        take = min(len(pool), max(1, senior_cap // max(len(senior_pool), 1)))
        rows.extend(pool[:take])
        kept += take
    logger.info("M3KE 高中采样保留 %s 条（cap=%s）", kept, senior_cap)
    return rows


GAOKAO_SUBJECTIVE_SKIP = ("Chinese", "English")


def load_gaokao(root, subjective_cap: int) -> list[dict]:
    rows = []
    base = root / "data" / "raw" / "_gaokao_tmp" / "GAOKAO-Bench-main" / "Data"
    rng = random.Random(RANDOM_SEED)
    subjective_pool: list[dict] = []

    def subject_of(filename: str) -> str | None:
        for key, zh in SUBJECT_ZH.items():
            if key in filename:
                return zh
        return None

    for f in sorted((base / "Objective_Questions").glob("*.json")):
        subject = subject_of(f.name)
        if not subject:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            q = (ex.get("question") or "").strip()
            if len(q) < 15:
                continue
            rows.append(make_row(q, subject, "选择题", "高中", "gaokao",
                                 raw_type=ex.get("category", "单选题"),
                                 answer=ex.get("answer", "")))

    for f in sorted((base / "Subjective_Questions").glob("*.json")):
        subject = subject_of(f.name)
        if not subject or any(s in f.name for s in GAOKAO_SUBJECTIVE_SKIP):
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            q = (ex.get("question") or "").strip()
            if len(q) < 15:
                continue
            subjective_pool.append(make_row(q, subject, "解答题", "高中", "gaokao",
                                            raw_type=ex.get("category", "解答题"),
                                            answer=ex.get("answer", "")))
    rng.shuffle(subjective_pool)
    rows.extend(subjective_pool[:subjective_cap])
    # 总量裁剪：MCQ+解答 合计超过 1,500 时按学科分层随机采样，控制训练时长
    overall_cap = 1500
    if len(rows) > overall_cap:
        by_subject: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_subject[r["subject"]].append(r)
        rows = []
        take = max(1, overall_cap // max(len(by_subject), 1))
        for subject in sorted(by_subject):
            pool = by_subject[subject]
            rng.shuffle(pool)
            rows.extend(pool[:take])
    return rows


OPTION_MARKER = re.compile(r"(?:^|\n)\s*A[.．、]")
BLANK_MARKER = re.compile(r"_{3,}|填空")


def load_numina(root, cap: int) -> list[dict]:
    import pyarrow.parquet as pq
    shard = root / "data" / "raw" / "_numina_shard0.parquet"
    table = pq.read_table(shard, columns=["source", "problem"])
    df = table.to_pandas()
    pool = []
    for src, problem in zip(df["source"], df["problem"]):
        if src != "cn_k12":
            continue
        text = (problem or "").strip()
        if len(text) < 20 or len(text) > MAX_TEXT_CHARS:
            continue
        if OPTION_MARKER.search(text) or BLANK_MARKER.search(text):
            continue  # 选择题形态跳过（已有更优来源）；填空题形态本轮不入类型空间
        pool.append(make_row(text, "数学", "解答题", "", "numina", raw_type="解答题"))
        if len(pool) >= cap * 3:
            break
    rng = random.Random(RANDOM_SEED)
    rng.shuffle(pool)
    return pool[:cap]


def main() -> None:
    root = get_root()
    settings = get_settings()
    proc_dir = settings.abs_path(settings.data_processed_dir)

    existing_frames = {}
    existing_texts: set[str] = set()
    for split in ("train", "val", "test"):
        path = proc_dir / f"{split}.csv"
        with open(path, encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            existing_frames[split] = {"fieldnames": list(reader.fieldnames or []), "rows": list(reader)}
        for r in existing_frames[split]["rows"]:
            existing_texts.add(norm_text(r["text"]))

    # 幂等护栏：本脚本只允许在原始（未并入过新源）数据上运行一次
    prior_sources = {r.get("source", "") for r in existing_frames["train"]["rows"]}
    prior_sources.discard("")
    prior_sources.discard("k12edubench")
    if prior_sources:
        raise SystemExit(
            f"检测到已并入过新源数据（{sorted(prior_sources)}）。"
            "请先 git restore data/processed/train.csv data/processed/val.csv 后重跑。")
    new_rows = []
    counts: dict[str, int] = {}
    for name, loader in [
        ("cmmlu", lambda: load_cmmlu(root)),
        ("m3ke", lambda: load_m3ke(root, M3KE_SENIOR_CAP)),
        ("gaokao", lambda: load_gaokao(root, GAOKAO_SUBJECTIVE_CAP)),
        ("numina", lambda: load_numina(root, NUMINA_CAP)),
    ]:
        loaded = loader()
        counts[name] = len(loaded)
        new_rows.extend(loaded)
        logger.info("来源 %s：%s 条", name, len(loaded))

    before = len(new_rows)
    new_rows = [r for r in new_rows if norm_text(r["text"]) not in existing_texts]
    seen_new: set[str] = set()
    deduped = []
    for r in new_rows:
        key = norm_text(r["text"])
        if key in seen_new:
            continue
        seen_new.add(key)
        deduped.append(r)
    new_rows = deduped
    logger.info("新增原始 %s 条 → 去重后 %s 条", before, len(new_rows))

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in new_rows:
        groups[(r["source"], r["subject"])].append(r)
    rng = random.Random(RANDOM_SEED)
    train_add: list[dict] = []
    val_add: list[dict] = []
    for key in sorted(groups):
        pool = groups[key]
        rng.shuffle(pool)
        n_val = max(1, int(len(pool) * VAL_RATIO))
        val_add.extend(pool[:n_val])
        train_add.extend(pool[n_val:])

    for split, add_rows in (("train", train_add), ("val", val_add)):
        frame = existing_frames[split]
        for field in CSV_FIELDS:
            if field not in frame["fieldnames"]:
                frame["fieldnames"].append(field)
        for r in frame["rows"]:
            r.setdefault("grade_band", "")
            r.setdefault("source", "k12edubench")
        frame["rows"].extend(add_rows)
        path = proc_dir / f"{split}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=frame["fieldnames"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(frame["rows"])
        logger.info("%s.csv 已写：%s 行（新增 %s）", split, len(frame["rows"]), len(add_rows))

    labels_path = proc_dir / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    labels["grade_bands"] = ["初中", "高中"]
    labels["stats"]["n_train"] = len(existing_frames["train"]["rows"])
    labels["stats"]["n_val"] = len(existing_frames["val"]["rows"])
    labels["stats"]["n_test"] = len(existing_frames["test"]["rows"])
    labels["stats"]["n_samples"] = sum(
        len(existing_frames[s]["rows"]) for s in ("train", "val", "test"))
    labels["sources_v03"] = counts
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {"new_by_source": counts, "train_add": len(train_add), "val_add": len(val_add),
              "train_total": labels["stats"]["n_train"], "val_total": labels["stats"]["n_val"],
              "test_unchanged": labels["stats"]["n_test"]}
    (proc_dir / "merge_v03_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("下一步：训练 v0.3 → rebuild_model_version.py --gate")


if __name__ == "__main__":
    main()
