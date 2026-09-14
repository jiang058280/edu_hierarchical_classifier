"""把早期题库的字符串选项数组规范为 ``[{key,text}]``；默认只预检。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edu_core.storage.stores import get_engine


def normalize(values: list) -> list[dict] | None:
    if not values or all(isinstance(value, dict) for value in values):
        return None
    output = []
    for index, value in enumerate(values):
        match = re.match(r"^\s*([A-H])\s*[.．、:]\s*(.+)$", str(value), re.IGNORECASE)
        key = match.group(1).upper() if match else chr(ord("A") + index)
        content = match.group(2).strip() if match else str(value).strip()
        output.append({"key": key, "text": content})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id,options_json FROM questions WHERE options_json IS NOT NULL"
        )).mappings().all()
    updates = []
    for row in rows:
        values = json.loads(row["options_json"])
        normalized = normalize(values)
        if normalized is not None:
            updates.append({"id": int(row["id"]), "options": json.dumps(normalized, ensure_ascii=False)})
    print(f"待规范化题目：{len(updates)}")
    if args.commit and updates:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE questions SET options_json=:options WHERE id=:id"
            ), updates)
        print(f"规范化完成：{len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
