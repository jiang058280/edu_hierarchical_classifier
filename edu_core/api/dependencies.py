"""API 依赖：进程内滑动窗口限流 + 客户端标识。

单机部署用内存限流即可（多 worker 部署时每进程独立窗口，生产可换 Redis）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Header, Request

from edu_core.config.settings import get_settings

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


class RateLimitExceeded(Exception):
    """限流触发。"""

    def __init__(self, retry_after_seconds: int):
        self.retry_after = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds}s")


def client_key(request: Request, x_forwarded_for: str | None = Header(default=None)) -> str:
    """客户端标识：优先 X-Forwarded-For 首段（反代场景），否则取直连 IP。"""
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(key: str) -> None:
    """滑动窗口限流：超过 settings.rate_limit_per_minute 时抛 RateLimitExceeded。"""
    settings = get_settings()
    window = 60.0
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= int(settings.rate_limit_per_minute):
        retry_after = max(1, int(window - (now - bucket[0])))
        raise RateLimitExceeded(retry_after)
    bucket.append(now)


async def rate_limit(request: Request, x_forwarded_for: str | None = Header(default=None)) -> str:
    """FastAPI 依赖：按客户端限流并返回客户端标识。"""
    key = client_key(request, x_forwarded_for)
    enforce_rate_limit(key)
    return key
