"""初始化 MySQL：建库 + 执行 runtime_schema.sql。

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
from edu_core.storage.bootstrap import bootstrap_mysql_schema

if __name__ == "__main__":
    summary = bootstrap_mysql_schema(get_settings())
    print("初始化完成：", summary)
