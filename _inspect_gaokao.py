"""检查 GAOKAO-Bench JSON 结构。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
base = Path("data/raw/_gaokao_tmp/GAOKAO-Bench-main/Data")

for sub in sorted(p.name for p in base.iterdir() if p.is_dir()):
    print(f"== Data/{sub} ==")
    d = base / sub
    for f in sorted(d.glob("*.json"))[:6]:
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "example" in data:
            ex = data["example"][0]
            print(f"  {f.name}: {len(data['example'])} 题 | 字段: {list(ex.keys())}")
        else:
            print(f"  {f.name}: 结构 {type(data).__name__}")
