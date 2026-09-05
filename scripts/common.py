"""脚本公共工具：把项目根目录加入 sys.path，保证 `python scripts\\x.py` 可直接导入 edu_core。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def get_root() -> Path:
    return PROJECT_ROOT
