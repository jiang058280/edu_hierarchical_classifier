"""历史数据迁移：把旧版 logs/feedback.jsonl 的反馈记录导入 MySQL。

旧版反馈无 schema 约束、无关联（不知道哪道题、哪个模型版本），
导入时 classification_id 置空，字段尽量映射（question_text/subject/correct_flag）。

用法：
    venv\\Scripts\\python scripts\\migrate_legacy_data.py [--file logs/feedback.jsonl] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.logging_config import get_logger
from edu_core.config.settings import get_settings
from edu_core.storage.stores import StoreBundle

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="旧版反馈历史导入 MySQL")
    parser.add_argument("--file", default="logs/feedback.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = Path(args.file)
    if not src.is_absolute():
        src = get_root() / src
    if not src.is_file():
        print(f"旧反馈文件不存在，跳过迁移：{src}")
        return

    records: list[dict] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("跳过非法 JSON 行：%s", line[:80])
    print(f"旧反馈记录共 {len(records)} 条")

    if args.dry_run:
        for r in records[:5]:
            print("  样例：", json.dumps(r, ensure_ascii=False)[:160])
        return

    stores = StoreBundle(settings=get_settings())
    inserted = 0
    for r in records:
        # 旧 schema：{question_text, subject, correct_flag, timestamp}
        stores.feedback.insert(
            classification_id=None,  # 旧数据无关联
            is_correct=bool(r.get("correct_flag")),
            question_text=r.get("question_text"),
            subject=r.get("subject"),
            model_version="v0.1-legacy",
        )
        inserted += 1
    print(f"已导入 {inserted} 条反馈（classification_id 为空，model_version=v0.1-legacy）")


if __name__ == "__main__":
    main()
