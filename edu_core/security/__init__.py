"""安全模块公共导出（密码哈希 / JWT / FastAPI 依赖 / 引导管理员）。"""

from edu_core.security.auth import (
    create_access_token,
    decode_access_token,
    ensure_bootstrap_admin,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)

__all__ = [
    "create_access_token",
    "decode_access_token",
    "ensure_bootstrap_admin",
    "get_current_user",
    "hash_password",
    "require_admin",
    "verify_password",
]
