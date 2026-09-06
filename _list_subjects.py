import csv
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
files = sorted(Path("data/raw/_cmmlu_tmp/CMMLU-master/data/test").glob("*.csv"))
out = []
for f in files:
    with open(f, encoding="utf-8") as fh:
        n = sum(1 for _ in csv.DictReader(fh))
    out.append(f"{f.stem}: {n}")
print("\n".join(out))
