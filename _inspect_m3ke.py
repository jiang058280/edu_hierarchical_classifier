"""按层级统计 M3KE 覆盖（层级在文件名第三段）。"""
import json
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
base = Path("data/raw/_m3ke_tmp/M3KE-main/data")
split = "test"

by_level: dict[str, int] = {}
by_file: dict[str, int] = {}
for f in sorted((base / split).glob("*.jsonl")):
    parts = f.stem.split("-")
    level = parts[-1].strip() if len(parts) >= 3 else "?"
    n = 0
    for ln in open(f, encoding="utf-8"):
        if ln.strip():
            n += 1
    by_level[level] = by_level.get(level, 0) + n
    if "high" in level.lower() or "junior" in level.lower():
        by_file[f.stem] = n

print("按层级:", json.dumps(by_level, ensure_ascii=False, indent=1))
print("\n初高中任务明细:")
total = 0
for name, n in by_file.items():
    print(f"  {name}: {n}")
    total += n
print(f"初高中合计: {total}")
