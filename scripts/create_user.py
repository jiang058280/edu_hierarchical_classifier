"""创建/管理登录用户（改进计划 WP-D）。

用法：
    venv\\Scripts\\python scripts\\create_user.py --username admin --password xxx --role admin
    venv\\Scripts\\python scripts\\create_user.py --username tom --password xxx --role teacher
    venv\\Scripts\\python scripts\\create_user.py --reset-password --username admin --password newxxx
"""

from __future__ import annotations

import argparse
import sys as _sys
from pathlib import Path as _Path
for _parent in _Path(__file__).resolve().parents:
    if (_parent / "edu_core").is_dir():
        _sys.path.insert(0, str(_parent))
        break
from scripts.common import get_root  # noqa: F401  — 确保 edu_core 可导入

from edu_core.security.auth import hash_password
from edu_core.storage.stores import StoreBundle


def main() -> None:
    parser = argparse.ArgumentParser(description="创建用户 / 重置密码（需 MySQL 已初始化）")
    parser.add_argument("--username", required=True, help="用户名（≥2 字符）")
    parser.add_argument("--password", required=True, help="密码（≥6 位，仅命令行传入，注意历史记录）")
    parser.add_argument("--role", default="teacher", choices=["admin", "teacher", "student"])
    parser.add_argument("--reset-password", action="store_true",
                        help="用户已存在时重置其密码（不新建）")
    args = parser.parse_args()

    if len(args.username) < 2:
        raise SystemExit("用户名至少 2 字符")
    if len(args.password) < 6:
        raise SystemExit("密码至少 6 位")

    users = StoreBundle().users
    existing = users.get_by_username(args.username)
    if existing and args.reset_password:
        with users.engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text(
                "UPDATE users SET password_hash = :p WHERE username = :u"),
                {"p": hash_password(args.password), "u": args.username})
        print(f"已重置密码：{args.username}（角色 {existing['role']}）")
        return
    if existing:
        raise SystemExit(f"用户名已存在：{args.username}（如需重置密码请加 --reset-password）")

    uid = users.create(args.username, hash_password(args.password), role=args.role)
    print(f"已创建用户：{args.username}（role={args.role}, uid={uid}）")


if __name__ == "__main__":
    main()
