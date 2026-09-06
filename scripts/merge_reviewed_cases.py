"""复核通过的 Bad Case 回灌训练集（改进计划 WP-F）。

流程（反馈闭环最后一公里）：
    export_bad_cases.py 导出 eval_sets/bad_cases.json
        → 人工复核：review_status 置为 approved，填 corrected_* 字段
        → 本脚本把 approved 样本修正到 train.csv（labels_source=manual）

规则：
  - 仅采纳 review_status == "approved" 且至少带一个 corrected_* 字段的样本；
  - 以导出的题目文本（≤500 字符预览）前缀匹配 train.csv 原行，仅做**原地修正**；
    找不到原行的样本明确拒绝（绝不把预览文本当完整题干新增入库）；
  - corrected 为空的级别保留原标签；人工标签必须存在于 labels.json 合法集合，
    否则拒绝该样本（防脏标签污染训练集）；
  - 幂等：已存在 labels_source=manual 且同文本前缀的行不重复处理；
  - 回灌后数据指纹变化，需重跑 scripts/preprocess_all.py 刷新 data_manifest.json。

用法：
    venv\\Scripts\\python scripts\\merge_reviewed_cases.py [--input eval_sets/bad_cases.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys as _sys
from pathlib import Path
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

from edu_core.config.settings import get_settings

MATCH_PREFIX_LEN = 120


def main() -> None:
    parser = argparse.ArgumentParser(description="复核通过样本回灌训练集（labels_source=manual）")
    parser.add_argument("--input", default="eval_sets/bad_cases.json")
    parser.add_argument("--dry-run", action="store_true", help="只统计将并入的样本，不写文件")
    args = parser.parse_args()

    root = get_root()
    src = Path(args.input)
    if not src.is_absolute():
        src = root / src
    if not src.is_file():
        raise SystemExit(f"Bad Case 文件不存在：{src}（先运行 scripts/export_bad_cases.py）")

    settings = get_settings()
    proc_dir = settings.abs_path(settings.data_processed_dir)
    labels = json.loads((proc_dir / "labels.json").read_text(encoding="utf-8"))
    legal = {
        "subject": set(labels["subjects"]),
        "question_type": set(labels["question_types"]),
        "knowledge_point": set(labels["knowledge_points"]),
    }

    payload = json.loads(src.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    train_csv = proc_dir / "train.csv"

    with open(train_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if "labels_source" not in fieldnames:
        fieldnames.append("labels_source")

    manual_prefixes = {(r["text"] or "")[:MATCH_PREFIX_LEN] for r in rows
                       if r.get("labels_source") == "manual"}

    applied, skipped_done, rejected = [], [], []
    for case in cases:
        if case.get("review_status") != "approved":
            continue
        corrected = case.get("corrected") or {}
        if not any(corrected.get(k) for k in legal):
            continue  # 无人工修正，不算回灌样本
        text_preview = (case.get("text") or "").strip()
        if not text_preview:
            continue
        prefix = text_preview[:MATCH_PREFIX_LEN]
        if prefix in manual_prefixes:
            skipped_done.append(text_preview[:60])
            continue
        idx = next((i for i, r in enumerate(rows) if r["text"].startswith(prefix)), None)
        if idx is None:
            rejected.append({"reason": "train_row_not_found", "text": text_preview[:60]})
            continue
        new_labels, invalid = {}, []
        for key, legal_set in legal.items():
            human = (corrected.get(key) or "").strip()
            if not human:
                continue
            if human not in legal_set:
                invalid.append(f"{key}={human}")
            else:
                new_labels[key] = human
        if invalid:
            rejected.append({"reason": f"illegal_label: {', '.join(invalid)}",
                             "text": text_preview[:60]})
            continue
        rows[idx].update(new_labels)
        rows[idx]["labels_source"] = "manual"
        manual_prefixes.add(prefix)
        applied.append(text_preview[:60])

    manual_total = sum(1 for r in rows if r.get("labels_source") == "manual")
    print(f"Bad Case 总数 {len(cases)} | 回灌修正 {len(applied)} | "
          f"已处理过 {len(skipped_done)} | 拒绝 {len(rejected)}")
    for r in rejected[:10]:
        print(f"  拒绝：{r['reason']} | {r['text']}…")
    print(f"训练集人工标签行数：{manual_total}/{len(rows)}"
          f"（manual 占比 {manual_total / max(len(rows), 1):.2%}）")

    if args.dry_run:
        print("dry-run：未写入任何文件")
        return
    if not applied:
        print("无样本需要回灌")
        return

    with open(train_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"train.csv 已更新：{train_csv}")
    print("⚠️ 数据指纹已变化：请重跑 scripts/preprocess_all.py 刷新 data_manifest.json，"
          "再用新版本号训练（train.py 会把最新 data_ref 写入版本 manifest）")


if __name__ == "__main__":
    main()
