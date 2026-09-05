"""结构化日志：控制台（GBK 安全）+ 文件（logs/edu_core.log）。

替代旧版 src/utils.write_log 的散装追加写法：
- 统一通过 get_logger(name) 获取带层级命名空间的 logger；
- 全局只配置一次，重复调用不会叠加 handler；
- Windows 控制台 GBK 编码下打印失败时降级替换，不影响主流程。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False
_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _SafeConsoleHandler(logging.StreamHandler):
    """控制台 handler：编码失败时用 ASCII 替换，避免 GBK 控制台抛 UnicodeEncodeError。"""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            super().emit(record)
        except UnicodeEncodeError:
            msg = self.format(record).encode("ascii", errors="replace").decode()
            sys.stderr.write(msg + "\n")


def configure_logging(logs_dir: Path | None = None, level: int = logging.INFO) -> None:
    """全局日志初始化（进程内只生效一次）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("edu")
    root.setLevel(level)
    root.propagate = False

    console = _SafeConsoleHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(console)

    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logs_dir / "edu_core.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取 edu 命名空间下的模块 logger。"""
    configure_logging()
    if name.startswith("edu."):
        return logging.getLogger(name)
    return logging.getLogger(f"edu.{name}")
