"""鉴权纯逻辑测试（改进计划 WP-D，不依赖数据库）。

覆盖：bcrypt 哈希往返、JWT 签发/解码/过期/篡改、preflight 弱密钥拒绝、
以及 FastAPI 依赖层（get_current_user / require_admin）在依赖注入下的
401/403 语义（通过直接调用依赖函数模拟）。
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from edu_core.config.preflight import PreflightError, validate_auth_settings
from edu_core.config.settings import Settings
from edu_core.security.auth import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)

SECRET = "unit-test-secret-0123456789abcdef-32bytes-min"
SECRET2 = "another-unit-test-secret-0123456789abcdef"


def _settings(**overrides) -> Settings:
    base = {"jwt_secret": SECRET, "auth_disabled": False}
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------

def test_password_hash_roundtrip():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert h.startswith("$2")  # bcrypt 标识
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong-pass", h)


def test_verify_password_handles_garbage_hash():
    assert not verify_password("x", "not-a-bcrypt-hash")
    assert not verify_password("", "$2b$12$abcdefghijklmnopqrstuv")
    assert not verify_password("x", "")


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def test_token_roundtrip_carries_identity():
    s = _settings(token_expire_minutes=30)
    token = create_access_token("alice", "teacher", 7, s)
    payload = decode_access_token(token, s)
    assert payload["sub"] == "alice"
    assert payload["role"] == "teacher"
    assert payload["uid"] == 7
    assert payload["exp"] > payload["iat"]


def test_expired_token_rejected():
    s = _settings(token_expire_minutes=-1)  # 立即过期
    token = create_access_token("bob", "admin", 1, s)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(token, s)


def test_tampered_token_rejected():
    s = _settings()
    token = create_access_token("bob", "admin", 1, s)
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(token + "x", s)


def test_wrong_secret_rejected():
    token = create_access_token("bob", "admin", 1, _settings())
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(token, _settings(jwt_secret=SECRET2))


# ---------------------------------------------------------------------------
# preflight 鉴权配置
# ---------------------------------------------------------------------------

def test_preflight_rejects_weak_secret():
    for bad in ("", "change-me", "short"):
        with pytest.raises(PreflightError):
            validate_auth_settings(_settings(jwt_secret=bad))


def test_preflight_accepts_strong_secret():
    summary = validate_auth_settings(_settings())
    assert summary["ok"] is True


def test_preflight_allows_disabled_mode_with_warning():
    summary = validate_auth_settings(_settings(jwt_secret="", auth_disabled=True))
    assert summary == {"auth": "disabled", "ok": True}


# ---------------------------------------------------------------------------
# 依赖层语义（monkeypatch 掉 DB 用户查询，纯逻辑）
# ---------------------------------------------------------------------------

class _FakeUserStore:
    def __init__(self, users):
        self._users = users

    def get_by_username(self, username):
        return self._users.get(username)


class _FakeBundle:
    def __init__(self, users):
        self.users = _FakeUserStore(users)


def _with_user(monkeypatch, users, settings=None):
    import edu_core.security.auth as auth_mod
    s = settings or _settings()
    monkeypatch.setattr(auth_mod, "StoreBundle", lambda settings=None: _FakeBundle(users))
    # get_current_user 内部经 get_settings() 取全局单例，测试中替换为受控 settings
    monkeypatch.setattr(auth_mod, "get_settings", lambda: s)
    return s


def test_get_current_user_ok(monkeypatch):
    s = _with_user(monkeypatch, {"alice": {
        "id": 7, "username": "alice", "role": "teacher",
        "password_hash": "x", "is_active": 1}})
    token = create_access_token("alice", "teacher", 7, s)
    user = get_current_user(token)
    assert user == {"id": 7, "username": "alice", "role": "teacher"}


def test_get_current_user_role_changed_forces_relogin(monkeypatch):
    _with_user(monkeypatch, {"alice": {
        "id": 7, "username": "alice", "role": "admin",  # 签发后角色被改
        "password_hash": "x", "is_active": 1}})
    s = _settings()
    token = create_access_token("alice", "teacher", 7, s)
    with pytest.raises(HTTPException) as exc:
        get_current_user(token)
    assert exc.value.status_code == 401


def test_get_current_user_missing_token_401(monkeypatch):
    _with_user(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        get_current_user(None)
    assert exc.value.status_code == 401


def test_require_admin_403_for_teacher():
    with pytest.raises(HTTPException) as exc:
        require_admin({"id": 7, "username": "alice", "role": "teacher"})
    assert exc.value.status_code == 403
    user = require_admin({"id": 1, "username": "root", "role": "admin"})
    assert user["role"] == "admin"


def test_token_iat_not_in_future():
    s = _settings()
    payload = decode_access_token(create_access_token("a", "teacher", 1, s), s)
    assert payload["iat"] <= int(time.time()) + 5
