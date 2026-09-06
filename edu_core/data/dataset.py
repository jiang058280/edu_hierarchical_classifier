"""数据加载与预处理（自 src/data_loader.py 平移）。

功能：
  1. 读取 K-12EduBench 9 个学科 JSON（data/raw/k12edubench/）
  2. 统一标签映射：学科（一级）/ 题型（二级，规则推断）/ 知识点（三级，一级知识点+学科前缀）
  3. 数据清洗：去重、去空、小类合并
  4. 按学科分层划分 train/val/test（70/15/15）
  5. 输出 data/processed/{train,val,test}.csv、labels.json、cleaning_report.json

与旧版的差异：
- 移除模块级路径副作用，路径由 settings 注入；
- 核心清洗/映射函数保持纯函数（不依赖全局状态），便于 pytest 直接覆盖。

运行：venv\\Scripts\\python -m edu_core.data.dataset
"""

from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings, get_settings

logger = get_logger(__name__)

SUBJECT_FILES = [
    "Mathematics", "Physics", "Chemistry", "Biology", "Chinese",
    "English", "History", "Geography", "Politics",
]

# 合并阈值
KNOWLEDGE_MIN_COUNT = 15   # 学科内知识点类最小样本数，低于则并入"其他"
TYPE_MIN_COUNT = 50        # 题型全局最小样本数，低于则并入"解答题"
RANDOM_SEED = 42

# ---------- 题型规则推断 ----------
OPTION_PATTERN = re.compile(r"[\s\n\r]+[A-F][、.．:]")
FILL_PATTERN = re.compile(r"填空|____|＿")
PROVE_PATTERN = re.compile(r"证明|求证")

# 学科名统一映射（各学科文件叫法不一致）
SUBJECT_ALIASES = {
    "高中英语": "英语",
    "高中语文": "语文",
}


def infer_question_type(raw_type: str, text: str) -> str:
    """由原始题型（客观题/主观题）+ 文本特征推断细化题型（纯函数）。"""
    if raw_type == "客观题":
        return "选择题" if OPTION_PATTERN.search(text) else "判断题"
    if FILL_PATTERN.search(text):
        return "填空题"
    if PROVE_PATTERN.search(text):
        return "证明题"
    return "解答题"


def clean_text(text: str) -> str:
    """文本清洗：去首尾空白、折叠多余空白行（纯函数）。"""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _get(item: dict, *keys: str) -> str | None:
    """兼容字段名差异的安全取值：依次尝试多个 key，取首个非空值。"""
    for k in keys:
        if k in item:
            v = item[k]
            if isinstance(v, str):
                v = v.strip()
            if v:
                return v
    return None


def load_raw_samples(raw_dir: Path) -> list[dict]:
    """读取全部 9 个学科 JSON，返回标准样本列表（兼容字段名差异）。"""
    samples: list[dict] = []
    for subj in SUBJECT_FILES:
        fp = raw_dir / f"{subj}.json"
        if not fp.exists() or fp.stat().st_size < 100:
            logger.warning("缺少数据文件：%s，跳过", fp)
            continue
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            text = clean_text(_get(item, "试题题目内容") or "")
            subject = _get(item, "学科") or ""
            subject = SUBJECT_ALIASES.get(subject, subject)
            raw_type = _get(item, "题型") or ""
            kp1 = _get(item, "一级知识点", "一级知识点 ") or ""
            answer = _get(item, "答案", "试题答案") or ""
            if not text or not subject or not kp1 or not raw_type:
                continue
            samples.append({
                "text": text,
                "subject": subject,
                "question_type": infer_question_type(raw_type, text),
                "raw_question_type": raw_type,
                "knowledge_point": f"{subject}::{kp1}",
                "answer": answer,
            })
        logger.info("加载 %s: %s 条", subj, len(data))
    return samples


def dedup(samples: list[dict]) -> list[dict]:
    """按题目文本去重（纯函数）。"""
    seen: set[str] = set()
    out = []
    for s in samples:
        if s["text"] in seen:
            continue
        seen.add(s["text"])
        out.append(s)
    return out


def merge_small_knowledge(samples: list[dict], min_count: int = KNOWLEDGE_MIN_COUNT) -> list[dict]:
    """学科内一级知识点样本过少 -> 并入 学科::其他（纯函数）。"""
    cnt = Counter(s["knowledge_point"] for s in samples)
    merged = 0
    for s in samples:
        subj, _, _ = s["knowledge_point"].partition("::")
        if cnt[s["knowledge_point"]] < min_count:
            s["knowledge_point"] = f"{subj}::其他"
            merged += 1
    if merged:
        logger.info("知识点小类合并：%s 条并入 学科::其他（阈值 %s）", merged, min_count)
    return samples


def merge_small_types(samples: list[dict], min_count: int = TYPE_MIN_COUNT) -> list[dict]:
    """全局题型样本过少 -> 并入解答题（纯函数）。"""
    cnt = Counter(s["question_type"] for s in samples)
    merged = 0
    for s in samples:
        if cnt[s["question_type"]] < min_count and s["question_type"] != "解答题":
            s["question_type"] = "解答题"
            merged += 1
    if merged:
        logger.info("题型小类合并：%s 条并入 解答题（阈值 %s）", merged, min_count)
    return samples


def stratified_split(samples: list[dict], ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
                     seed: int = RANDOM_SEED) -> tuple[list[dict], list[dict], list[dict]]:
    """按学科分层抽样，划分 训练/验证/测试（纯函数，固定随机种子可复现）。"""
    rng = random.Random(seed)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_subject[s["subject"]].append(s)
    train: list[dict] = []
    val: list[dict] = []
    test: list[dict] = []
    for _, group in by_subject.items():
        rng.shuffle(group)
        n = len(group)
        n_train = int(n * ratios[0])
        n_val = int(n * ratios[1])
        train.extend(group[:n_train])
        val.extend(group[n_train:n_train + n_val])
        test.extend(group[n_train + n_val:])
    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)
    return train, val, test


def build_labels(train_samples: list[dict], all_samples: list[dict]) -> dict:
    """构建标签映射（基于全部样本的类别全集）（纯函数）。"""
    subjects = sorted({s["subject"] for s in all_samples})
    qtypes = sorted({s["question_type"] for s in all_samples})
    kps = sorted({s["knowledge_point"] for s in all_samples})

    subject_types: dict[str, set[str]] = defaultdict(set)
    subject_knowledge: dict[str, set[str]] = defaultdict(set)
    for s in all_samples:
        subject_types[s["subject"]].add(s["question_type"])
        subject_knowledge[s["subject"]].add(s["knowledge_point"])

    return {
        "subjects": subjects,
        "subject2id": {x: i for i, x in enumerate(subjects)},
        "question_types": qtypes,
        "type2id": {x: i for i, x in enumerate(qtypes)},
        "knowledge_points": kps,
        "knowledge2id": {x: i for i, x in enumerate(kps)},
        "subject_types": {k: sorted(v) for k, v in subject_types.items()},
        "subject_knowledge": {k: sorted(v) for k, v in subject_knowledge.items()},
        "stats": {
            "n_samples": len(all_samples),
            "n_train": len(train_samples),
            "n_val": 0,
            "n_test": 0,
            "per_subject": dict(Counter(s["subject"] for s in all_samples)),
            "per_type": dict(Counter(s["question_type"] for s in all_samples)),
            "per_knowledge": dict(Counter(s["knowledge_point"] for s in all_samples)),
        },
        "rule": {
            "question_type": "规则推断：客观题含选项->选择题，客观题无选项->判断题；主观题含填空->填空题，含证明->证明题，其余->解答题",
            "knowledge_point": f"一级知识点，跨学科加 '学科::' 前缀防冲突；样本<{KNOWLEDGE_MIN_COUNT} 并入 '学科::其他'",
        },
    }


def write_csv(path: Path, samples: list[dict]) -> None:
    """写出 CSV（UTF-8 with BOM，便于 Excel 打开）。

    labels_source 标记标签来源（改进计划 WP-F）：rule=规则推断，manual=人工复核修正。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "text", "subject", "question_type", "knowledge_point",
            "raw_question_type", "answer", "labels_source"])
        writer.writeheader()
        for s in samples:
            row = dict(s)
            row.setdefault("labels_source", "rule")
            writer.writerow(row)


def run(settings: Settings | None = None) -> dict:
    """完整数据预处理主流程：raw JSON -> processed CSV + labels.json + 清洗报告。"""
    settings = settings or get_settings()
    raw_dir = settings.abs_path(settings.data_processed_dir).parent / "raw" / "k12edubench"
    proc_dir = settings.abs_path(settings.data_processed_dir)
    proc_dir.mkdir(parents=True, exist_ok=True)

    logger.info("开始数据加载与预处理...")
    samples = load_raw_samples(raw_dir)
    logger.info("原始有效样本：%s 条", len(samples))

    before = len(samples)
    samples = dedup(samples)
    logger.info("去重后：%s 条（去除 %s）", len(samples), before - len(samples))

    samples = merge_small_knowledge(samples)
    samples = merge_small_types(samples)

    train, val, test = stratified_split(samples)
    logger.info("划分完成：训练 %s / 验证 %s / 测试 %s", len(train), len(val), len(test))

    labels = build_labels(train, samples)
    labels["stats"]["n_val"] = len(val)
    labels["stats"]["n_test"] = len(test)

    write_csv(proc_dir / "train.csv", train)
    write_csv(proc_dir / "val.csv", val)
    write_csv(proc_dir / "test.csv", test)

    with open(proc_dir / "labels.json", "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)

    report = {
        "raw_valid": before,
        "after_dedup": len(samples),
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "n_subjects": len(labels["subjects"]),
        "n_types": len(labels["question_types"]),
        "n_knowledge": len(labels["knowledge_points"]),
        "per_subject": labels["stats"]["per_subject"],
        "per_type": labels["stats"]["per_type"],
        "per_knowledge_top20": dict(sorted(
            labels["stats"]["per_knowledge"].items(), key=lambda x: -x[1])[:20]),
    }
    with open(proc_dir / "cleaning_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("学科 %s 类 / 题型 %s 类 / 知识点 %s 类，输出至 %s",
                report["n_subjects"], report["n_types"], report["n_knowledge"], proc_dir)
    return report


if __name__ == "__main__":
    run()
