"""初始化 MySQL：建库 + 按序应用 edu_core/storage/migrations/V*__*.sql（幂等）。

用法：
    venv\\Scripts\\python scripts\\init_db.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入
from edu_core.config.settings import get_settings
from edu_core.storage.bootstrap import apply_pending_migrations

if __name__ == "__main__":
    summary = apply_pending_migrations(get_settings())
    print("初始化完成：")
    print("  数据库：", summary["database"])
    print("  迁移全集：", ", ".join(summary["migrations_total"]))
    print("  本次应用：", ", ".join(summary["migrations_applied_now"]) or "（已是最新，无新增）")
