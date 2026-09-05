# -*- coding: utf-8 -*-
"""
教育题目层级分类系统 - 数据加载与预处理
功能：
  1. 读取 K-12EduBench 9 个学科 JSON（data/raw/k12edubench/）
  2. 统一标签映射：学科（一级）/ 题型（二级，规则推断）/ 知识点（三级，一级知识点+学科前缀）
  3. 数据清洗：去重、去空、小类合并
  4. 分层划分训练/验证/测试集（70/15/15）
  5. 输出 data/processed/{train,val,test}.csv 与 data/labels.json、清洗报告

运行：venv/Scripts/python src/data_loader.py
"""
# 注：K-12EduBench 不同学科文件字段名存在细微差异
#   （如 "一级知识点 " 带尾随空格、"答案" vs "试题答案"），需兼容读取。
import os
import sys
import json
import re
import csv
import random
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import get_project_root, setup_environment, write_log

ROOT = get_project_root()
setup_environment()

RAW_DIR = os.path.join(ROOT, "data", "raw", "k12edubench")
PROC_DIR = os.path.join(ROOT, "data", "processed")
os.makedirs(PROC_DIR, exist_ok=True)

SUBJECT_FILES = [
    "Mathematics", "Physics", "Chemistry", "Biology", "Chinese",
    "English", "History", "Geography", "Politics",
]

# 合并阈值
KNOWLEDGE_MIN_COUNT = 15   # 学科内知识点类最小样本数，低于则并入"其他"
TYPE_MIN_COUNT = 50        # 题型全局最小样本数，低于则并入"解答题"
RANDOM_SEED = 42

# ---------- 题型规则推断 ----------
# 客观题含选项 → 选择题；客观题无选项 → 判断题；主观题按关键词细分
OPTION_PATTERN = re.compile(r"[\s\n\r]+[A-F][、.．:]")
FILL_PATTERN = re.compile(r"填空|____|＿")
PROVE_PATTERN = re.compile(r"证明|求证")


def infer_question_type(raw_type: str, text: str) -> str:
    """由原始题型（客观题/主观题）+ 文本特征推断细化题型"""
    if raw_type == "客观题":
        return "选择题" if OPTION_PATTERN.search(text) else "判断题"
    # 主观题
    if FILL_PATTERN.search(text):
        return "填空题"
    if PROVE_PATTERN.search(text):
        return "证明题"
    return "解答题"


def clean_text(text: str) -> str:
    """文本清洗：去首尾空白、折叠多余空白行"""
    if not text:
        return ""
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _get(item: dict, *keys: str) -> str:
    """兼容字段名差异的安全取值：依次尝试多个 key，取首个非空值"""
    for k in keys:
        if k in item:
            v = item[k]
            if isinstance(v, str):
                v = v.strip()
            if v:
                return v
    return None


# 学科名统一映射（K-12EduBench 各学科文件叫法不一致）
SUBJECT_ALIASES = {
    "高中英语": "英语",
    "高中语文": "语文",
}


def load_raw_samples() -> list:
    """读取全部 9 个学科 JSON，返回标准样本列表（兼容字段名差异）"""
    samples = []
    for subj in SUBJECT_FILES:
        fp = os.path.join(RAW_DIR, f"{subj}.json")
        if not os.path.exists(fp) or os.path.getsize(fp) < 100:
            write_log("data_loader", f"缺少数据文件：{fp}，跳过", "WARN")
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
        write_log("data_loader", f"加载 {subj}: {len(data)} 条")
    return samples


def dedup(samples: list) -> list:
    """按题目文本去重"""
    seen = set()
    out = []
    for s in samples:
        if s["text"] in seen:
            continue
        seen.add(s["text"])
        out.append(s)
    return out


def merge_small_knowledge(samples: list) -> list:
    """学科内一级知识点样本过少 → 并入 学科::其他"""
    cnt = Counter(s["knowledge_point"] for s in samples)
    # 按学科统计各知识点
    subj_cnt = defaultdict(Counter)
    for s in samples:
        subj, _, _ = s["knowledge_point"].partition("::")
        subj_cnt[subj][s["knowledge_point"]] += 1
    merged = 0
    for s in samples:
        subj, _, kp = s["knowledge_point"].partition("::")
        if cnt[s["knowledge_point"]] < KNOWLEDGE_MIN_COUNT:
            s["knowledge_point"] = f"{subj}::其他"
            merged += 1
    if merged:
        write_log("data_loader", f"知识点小类合并：{merged} 条并入 学科::其他（阈值 {KNOWLEDGE_MIN_COUNT}）")
    return samples


def merge_small_types(samples: list) -> list:
    """全局题型样本过少 → 并入解答题"""
    cnt = Counter(s["question_type"] for s in samples)
    merged = 0
    for s in samples:
        if cnt[s["question_type"]] < TYPE_MIN_COUNT and s["question_type"] != "解答题":
            s["question_type"] = "解答题"
            merged += 1
    if merged:
        write_log("data_loader", f"题型小类合并：{merged} 条并入 解答题（阈值 {TYPE_MIN_COUNT}）")
    return samples


def stratified_split(samples: list, ratios=(0.70, 0.15, 0.15)) -> tuple:
    """按学科分层抽样，划分 训练/验证/测试"""
    rng = random.Random(RANDOM_SEED)
    by_subject = defaultdict(list)
    for s in samples:
        by_subject[s["subject"]].append(s)
    train, val, test = [], [], []
    for subj, group in by_subject.items():
        rng.shuffle(group)
        n = len(group)
        n_train = int(n * ratios[0])
        n_val = int(n * ratios[1])
        train.extend(group[:n_train])
        val.extend(group[n_train:n_train + n_val])
        test.extend(group[n_train + n_val:])
    rng.shuffle(train); rng.shuffle(val); rng.shuffle(test)
    return train, val, test


def build_labels(train_samples, all_samples) -> dict:
    """构建标签映射（基于全部样本的类别全集）"""
    subjects = sorted(set(s["subject"] for s in all_samples))
    qtypes = sorted(set(s["question_type"] for s in all_samples))
    kps = sorted(set(s["knowledge_point"] for s in all_samples))

    subject_types = defaultdict(set)
    subject_knowledge = defaultdict(set)
    for s in all_samples:
        subject_types[s["subject"]].add(s["question_type"])
        subject_knowledge[s["subject"]].add(s["knowledge_point"])

    labels = {
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
            "n_train": 0, "n_val": 0, "n_test": 0,
            "per_subject": dict(Counter(s["subject"] for s in all_samples)),
            "per_type": dict(Counter(s["question_type"] for s in all_samples)),
            "per_knowledge": dict(Counter(s["knowledge_point"] for s in all_samples)),
        },
        "rule": {
            "question_type": "规则推断：客观题含选项→选择题，客观题无选项→判断题；主观题含填空→填空题，含证明→证明题，其余→解答题",
            "knowledge_point": "一级知识点，跨学科加 '学科::' 前缀防冲突；样本<{} 并入 '学科::其他'".format(KNOWLEDGE_MIN_COUNT),
        },
    }
    return labels


def write_csv(path, samples):
    """写出 CSV（UTF-8 with BOM，便于 Excel 打开）"""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "subject", "question_type", "knowledge_point", "raw_question_type", "answer"])
        writer.writeheader()
        for s in samples:
            writer.writerow(s)


def main():
    write_log("data_loader", "开始数据加载与预处理...")
    samples = load_raw_samples()
    write_log("data_loader", f"原始有效样本：{len(samples)} 条")

    before = len(samples)
    samples = dedup(samples)
    write_log("data_loader", f"去重后：{len(samples)} 条（去除 {before - len(samples)}）")

    samples = merge_small_knowledge(samples)
    samples = merge_small_types(samples)

    train, val, test = stratified_split(samples)
    write_log("data_loader", f"划分完成：训练 {len(train)} / 验证 {len(val)} / 测试 {len(test)}")

    labels = build_labels(train, samples)
    labels["stats"]["n_train"] = len(train)
    labels["stats"]["n_val"] = len(val)
    labels["stats"]["n_test"] = len(test)

    write_csv(os.path.join(PROC_DIR, "train.csv"), train)
    write_csv(os.path.join(PROC_DIR, "val.csv"), val)
    write_csv(os.path.join(PROC_DIR, "test.csv"), test)

    labels_path = os.path.join(PROC_DIR, "labels.json")
    with open(labels_path, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)

    # 清洗报告
    report = {
        "raw_valid": before,
        "after_dedup": len(samples),
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "n_subjects": len(labels["subjects"]),
        "n_types": len(labels["question_types"]),
        "n_knowledge": len(labels["knowledge_points"]),
        "per_subject": labels["stats"]["per_subject"],
        "per_type": labels["stats"]["per_type"],
        "per_knowledge_top20": dict(sorted(labels["stats"]["per_knowledge"].items(), key=lambda x: -x[1])[:20]),
    }
    with open(os.path.join(PROC_DIR, "cleaning_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    write_log("data_loader", "=" * 50)
    write_log("data_loader", f"学科 {report['n_subjects']} 类：{labels['subjects']}")
    write_log("data_loader", f"题型 {report['n_types']} 类：{labels['question_types']}")
    write_log("data_loader", f"知识点 {report['n_knowledge']} 类")
    write_log("data_loader", f"已完成，输出至 {PROC_DIR}")


if __name__ == "__main__":
    main()
