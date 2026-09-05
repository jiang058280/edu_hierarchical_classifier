"""阶段耗时记录器。

用法（对齐 knowforge pipeline 的分段计时思路）：

    timer = StageTimer()
    timer.mark("validate")        # 记录从起点到当前的耗时
    ... 执行下一阶段 ...
    timer.mark("inference")
    timer.total()                 # 总耗时 ms

供分类链路和评测脚本输出各阶段耗时，用于性能分析与瓶颈定位。
"""

from __future__ import annotations

import time


class StageTimer:
    """毫秒级阶段计时器。"""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start
        self.stages: dict[str, float] = {}

    def mark(self, name: str) -> float:
        """记录上一阶段耗时（ms），并把计时起点推进到当前。"""
        now = time.perf_counter()
        self.stages[name] = round((now - self._last) * 1000, 2)
        self._last = now
        return self.stages[name]

    def total(self) -> float:
        """从创建到当前的总耗时（ms）。"""
        return round((time.perf_counter() - self._start) * 1000, 2)

    def as_dict(self) -> dict[str, float]:
        """已记录阶段耗时 + 总耗时。"""
        data = dict(self.stages)
        data["total_ms"] = self.total()
        return data
