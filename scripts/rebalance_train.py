"""训练集题型重采样（B1：判断题重采样与重训）。

背景：v0.3 训练集判断题占比过低（256/9,893 ≈ 2.6%），golden set 判断题整题
错误导致题型宏平均 F1 从 0.9406 拉低到 0.8522。本脚本对 train.csv 做
过采样（只复制行、绝不修改文本与标注）：

  - 判断题：每行复制为 --judge-times 份（默认 4，即原行 + 3 份副本）；
  - 解答题：按 --solve-times（默认 1.5）倍率取整复制——按行序偶数行多补
    1 份副本，总量恰为 1.5 倍（2120 → 3180）；
  - 选择题：不动。

规则：
  1. 副本行追加标记列 oversampled=1（原行 =0），文本与各标签列不变；
  2. 写回前把原文件备份为 train.pre_rebalance.csv；备份已存在时拒绝执行
     （防二次运行叠加复制），如需重做请先手动从备份还原；
  3. 复制统计写入 data/processed/rebalance_report.json。

运行：
    venv\\Scripts\\python -X utf8 scripts\\rebalance_train.py
    venv\\Scripts\\python -X utf8 scripts\\rebalance_train.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys as _sys
from datetime import datetime
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root

ROOT = get_root()
TRAIN_CSV = ROOT / "data" / "processed" / "train.csv"
BACKUP_CSV = ROOT / "data" / "processed" / "train.pre_rebalance.csv"
REPORT_JSON = ROOT / "data" / "processed" / "rebalance_report.json"

OVERSAMPLE_COL = "oversampled"


def copies_for(row_index: int, times: float) -> int:
    """按倍率计算该行总份数（≥1 整数）。1.5 倍率下偶数行 2 份、奇数行 1 份。"""
    whole = int(times)
    frac = times - whole
    extra = 1 if frac > 0 and row_index % 2 == 0 else 0
    return whole + extra


def main() -> None:
    parser = argparse.ArgumentParser(description="train.csv 题型重采样（判断题×4 / 解答题×1.5）")
    parser.add_argument("--judge-times", type=float, default=4.0, help="判断题总倍数（默认 4）")
    parser.add_argument("--solve-times", type=float, default=1.5, help="解答题总倍数（默认 1.5）")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = parser.parse_args()

    if BACKUP_CSV.is_file():
        raise SystemExit(
            f"备份已存在：{BACKUP_CSV}\n"
            "为防二次运行叠加复制，已拒绝执行；如确需重做，请先从备份还原 train.csv 后删除备份。")
    if not TRAIN_CSV.is_file():
        raise SystemExit(f"train.csv 不存在：{TRAIN_CSV}")

    with open(TRAIN_CSV, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if OVERSAMPLE_COL in fieldnames:
        raise SystemExit(f"train.csv 已含 {OVERSAMPLE_COL} 列，疑似已重采样过，拒绝重复执行。")

    before = {}
    for row in rows:
        before[row["question_type"]] = before.get(row["question_type"], 0) + 1

    out_rows: list[dict] = []
    type_counter: dict[str, int] = {}
    copied = {}
    for row in rows:
        qtype = row["question_type"]
        seq = type_counter.get(qtype, 0)
        type_counter[qtype] = seq + 1
        times = {"判断题": args.judge_times, "解答题": args.solve_times}.get(qtype, 1.0)
        n = copies_for(seq, times)
        copied[qtype] = copied.get(qtype, 0) + n
        for k in range(n):
            new_row = dict(row)
            new_row[OVERSAMPLE_COL] = "0" if k == 0 else "1"
            out_rows.append(new_row)

    after = {}
    for row in out_rows:
        after[row["question_type"]] = after.get(row["question_type"], 0) + 1

    print(f"重采样前：{before}（共 {len(rows)}）")
    print(f"重采样后：{after}（共 {len(out_rows)}）")
    print(f"净增副本：{ {k: v - before.get(k, 0) for k, v in copied.items()} }")

    report = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backup": str(BACKUP_CSV.relative_to(ROOT)),
        "params": {"judge_times": args.judge_times, "solve_times": args.solve_times},
        "rows_before": len(rows), "rows_after": len(out_rows),
        "distribution_before": before, "distribution_after": after,
        "copies_added": {k: v - before.get(k, 0) for k, v in copied.items()},
        "note": "仅复制行（text/labels 不变），副本行 oversampled=1；val/test 未改动",
    }

    if args.dry_run:
        print("（dry-run：未写任何文件）")
        return

    # 1) 原文件字节级备份；2) 写回 train.csv；3) 落盘复制报告
    shutil.copy2(TRAIN_CSV, BACKUP_CSV)
    with open(TRAIN_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + [OVERSAMPLE_COL])
        writer.writeheader()
        writer.writerows(out_rows)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已备份原文件：{BACKUP_CSV}")
    print(f"复制报告已写入：{REPORT_JSON}")
    print("下一步：venv\\Scripts\\python scripts\\preprocess_all.py 刷新 data_manifest")


if __name__ == "__main__":
    main()
