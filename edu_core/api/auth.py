"""认证路由（改进计划 WP-D）。

POST /auth/login：OAuth2 密码表单（Swagger 的 Authorize 按钮可直接调试），
返回 JWT access_token；GET /auth/me：查看当前登录身份。
同一 router 以 /api/v1 与 /api 双前缀注册，与其他路由一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from edu_core.config.settings import get_settings
from edu_core.security.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from edu_core.storage.stores import StoreBundle

router = APIRouter()


@router.post("/auth/login", dependencies=[])
def login(form: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """账号密码登录，签发 JWT（鉴权总开关关闭时提示拒绝）。"""
    settings = get_settings()
    if settings.auth_disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="鉴权已通过 EDU_AUTH_DISABLED 关闭，无需登录",
        )
    if not form.username or not form.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="用户名与密码不能为空")
    users = StoreBundle(settings=settings).users
    user = users.get_by_username(form.username)
    # 用户不存在与密码错误统一文案，避免用户名枚举
    if not user or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误", headers={"WWW-Authenticate": "Bearer"})
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户已停用")
    token = create_access_token(user["username"], user["role"], int(user["id"]), settings)
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user["username"],
        "role": user["role"],
    }


@router.get("/auth/me")
def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """当前登录身份。"""
    return user


@router.post("/auth/users", dependencies=[])
def create_user(payload: dict, _: dict[str, Any] = Depends(require_admin)) -> dict:
    """管理员创建用户（username/password/role）。"""
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    role = payload.get("role") or "teacher"
    if len(username) < 2 or len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名至少 2 字符，密码至少 6 位")
    users = StoreBundle(settings=get_settings()).users
    uid = users.create(username, hash_password(password), role=role)
    return {"status": "ok", "id": uid, "username": username, "role": role}
