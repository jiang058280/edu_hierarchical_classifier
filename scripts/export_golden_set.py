"""从 data/processed/test.csv 固化 golden 回归测试集。

golden set 是模型变更的回归基准（对齐 knowforge 的 eval_sets/ 思路）：
- 固定随机种子按学科分层抽样 settings.golden_set_size 条；
- 与 test.csv 其余部分解耦，避免"用训练时见过的数据自测"；
- 输出 eval_sets/golden_test_set.json（入库 git，权重不入库、数据基准入库）。

用法：
    venv\\Scripts\\python scripts\\export_golden_set.py [--size 300]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import get_settings


def export(size: int, seed: int = 42) -> Path:
    settings = get_settings()
    test_csv = settings.abs_path(settings.data_processed_dir) / "test.csv"
    if not test_csv.is_file():
        raise FileNotFoundError(f"测试集不存在：{test_csv}")

    import csv

    with open(test_csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if size > len(rows):
        size = len(rows)

    # 按学科分层抽样（与训练划分同思路，保证学科覆盖均匀）
    rng = random.Random(seed)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_subject[r["subject"]].append(r)
    for group in by_subject.values():
        rng.shuffle(group)

    cases: list[dict] = []
    subjects = sorted(by_subject)
    i = 0
    while len(cases) < size:
        progressed = False
        for s in subjects:
            if i < len(by_subject[s]):
                row = by_subject[s][i]
                cases.append({
                    "text": row["text"],
                    "subject": row["subject"],
                    "question_type": row["question_type"],
                    "knowledge_point": row["knowledge_point"],
                })
                progressed = True
                if len(cases) >= size:
                    break
        if not progressed:
            break
        i += 1

    payload = {
        "dataset": "K-12EduBench test split (golden regression subset)",
        "source": str(test_csv.relative_to(get_root())),
        "size": len(cases),
        "seed": seed,
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cases": cases,
    }
    out_dir = settings.abs_path(settings.eval_sets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "golden_test_set.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"golden 测试集已导出：{out}（{len(cases)} 条，seed={seed}）")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="固化 golden 回归测试集")
    parser.add_argument("--size", type=int, default=get_settings().golden_set_size)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    export(args.size, args.seed)
