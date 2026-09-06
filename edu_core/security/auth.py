"""认证与权限（改进计划 WP-D）。

组成：
- 密码：bcrypt 哈希（绝不存明文）；
- 令牌：JWT（HS256，payload 含 sub/role/uid/exp）；
- FastAPI 依赖：``get_current_user``（登录即可）与 ``require_admin``（治理操作）；
- 引导管理员：首次启动且 users 表为空时，用 settings.admin_bootstrap_password 创建 admin。

总开关 ``EDU_AUTH_DISABLED=true`` 仅限本机开发调试（依赖返回匿名 admin），
生产必须关闭——preflight 会在鉴权开启时强校验 jwt_secret。

失败语义：鉴权失败抛 HTTPException(401/403)，由全局错误处理器统一转 {"error": ...}。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from edu_core.config.logging_config import get_logger
from edu_core.config.settings import Settings, get_settings
from edu_core.storage.stores import StoreBundle

logger = get_logger(__name__)

# tokenUrl 指向正式前缀的登录端点（Swagger Authorize 按钮据此调试）
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

_JWT_ALGORITHM = "HS256"
# preflight 拒绝的示例值（防止把模板密钥带上生产）
_WEAK_SECRETS = {"", "change-me-to-a-long-random-string", "change-me"}


# ---------------------------------------------------------------------------
# 密码（bcrypt）
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """bcrypt 哈希（自动含盐，结果含算法标识与成本因子）。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """校验密码；哈希格式非法时一律返回 False（不抛异常、不泄露信息）。"""
    if not plain or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(username: str, role: str, user_id: int,
                        settings: Settings | None = None) -> str:
    s = settings or get_settings()
    payload = {
        "sub": username,
        "role": role,
        "uid": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=int(s.token_expire_minutes)),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """解码并校验 token；无效/过期抛 jwt 异常（由 get_current_user 转 401）。"""
    s = settings or get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[_JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------

def _anonymous_admin() -> dict[str, Any]:
    return {"id": 0, "username": "anonymous", "role": "admin"}


def _credentials_exception(detail: str = "未登录或登录已过期") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(token: str | None = Depends(_oauth2_scheme)) -> dict[str, Any]:
    """登录即可访问的依赖：校验 JWT + 用户存在且启用。"""
    settings = get_settings()
    if settings.auth_disabled:
        return _anonymous_admin()
    if not token:
        raise _credentials_exception()
    try:
        payload = decode_access_token(token, settings)
    except jwt.ExpiredSignatureError:
        raise _credentials_exception("登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise _credentials_exception("无效的登录凭证")

    username: str | None = payload.get("sub")
    role: str | None = payload.get("role")
    if not username or not role:
        raise _credentials_exception("无效的登录凭证")

    user = StoreBundle(settings=settings).users.get_by_username(username)
    if not user or not user["is_active"]:
        raise _credentials_exception("用户不存在或已停用")
    if user["role"] != role:
        # 角色与令牌不一致（令牌签发后角色被改）→ 拒绝，强制重新登录
        raise _credentials_exception("用户角色已变更，请重新登录")
    return {"id": int(user["id"]), "username": user["username"], "role": user["role"]}


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """治理操作依赖：仅 admin 角色可通过。"""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def require_teacher(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """教师端依赖：teacher 或 admin 可通过（平台计划 M0 双门户）。"""
    if user.get("role") not in ("teacher", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该功能仅面向教师，请从教师门户登录",
        )
    return user


def require_student(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """学生端依赖：仅 student 角色可通过（平台计划 M0 双门户）。"""
    if user.get("role") != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该功能仅面向学生，请从学生门户登录",
        )
    return user


# ---------------------------------------------------------------------------
# 引导管理员
# ---------------------------------------------------------------------------

def ensure_bootstrap_admin(settings: Settings | None = None) -> dict[str, Any]:
    """users 表为空且配置了 EDU_ADMIN_BOOTSTRAP_PASSWORD 时创建初始 admin。

    启动期调用（app.warmup_runtime）。表为空但未配置密码时仅告警：
    系统可启动，但在运行 scripts/create_user.py 之前无人能登录。
    """
    s = settings or get_settings()
    users = StoreBundle(settings=s).users
    if users.count() > 0:
        return {"bootstrapped": False, "reason": "users_not_empty"}
    if not s.admin_bootstrap_password:
        logger.warning(
            "users 表为空且未配置 EDU_ADMIN_BOOTSTRAP_PASSWORD："
            "请运行 scripts/create_user.py 创建账号，否则无人可登录")
        return {"bootstrapped": False, "reason": "no_bootstrap_password"}
    uid = users.create("admin", hash_password(s.admin_bootstrap_password), role="admin")
    logger.info("已创建引导管理员 admin（uid=%s），请尽快用 create_user.py 管理账号", uid)
    return {"bootstrapped": True, "uid": uid}
