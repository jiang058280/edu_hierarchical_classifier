"""检查 CMMLU 数据结构与学科分布。"""
import csv
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
base = Path("data/raw/_cmmlu_tmp/CMMLU-master/data")

for split in ("dev", "test"):
    files = sorted((base / split).glob("*.csv"))
    print(f"== {split}: {len(files)} 个学科文件 ==")
    if split == "test":
        total = 0
        for f in files:
            with open(f, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if any(k in f.stem for k in ("middle_school", "high_school")):
                print(f"  {f.stem}: {len(rows)} 条 | 字段: {list(rows[0].keys())}")
                total += len(rows)
        print(f"  初高中学科合计: {total} 条")
